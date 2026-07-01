"""Replay count-intensity candidates through the live manual field policy.

This is a read-only manual-strategy diagnostic. It does not read
SportsPredict `/predictions` and never submits.

`count_intensity_replay.py` asks which tournament count multipliers produce the
best raw reconstructed model probabilities. This script asks the deployable
question: after applying the same family-specific `manual_decision()` policy
used by `full_manual_pricing.py` when field averages are visible, does any
candidate beat the current count-intensity map robustly enough to promote?

Usage:
    uv run --with requests --with numpy --with scipy --with pandas --with pyarrow --with statsmodels \
      python scripts/count_intensity_policy_replay.py --sports-predict --write
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest.history import HistoryMarket, brier, official_alt_rbp, official_delta_rbp  # noqa: E402
from count_intensity_replay import (  # noqa: E402
    MatchContext,
    PricedRow,
    build_contexts,
    candidate_maps,
    price_rows,
)
from count_policy_backtest import load_count_rows  # noqa: E402
from manual.policy import manual_decision  # noqa: E402
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402


OUT = ROOT / "data" / "logs" / "count_intensity_policy_replay.md"
BASELINE_CANDIDATE = "current_v4"


@dataclass(frozen=True)
class PolicyPricedRow:
    row: HistoryMarket
    candidate: str
    model_probability: float
    manual_probability: float
    policy: str


def apply_manual_policy(priced: list[PricedRow]) -> tuple[list[PolicyPricedRow], list[str]]:
    out: list[PolicyPricedRow] = []
    skipped: list[str] = []
    for item in priced:
        try:
            spec = parse(item.row.question, teams=ALL_TEAM_NAMES)
            decision = manual_decision(
                spec,
                item.probability,
                field_probability=item.row.crowd,
            )
        except Exception as exc:
            skipped.append(f"{item.row.match}: {item.row.question}: {exc}")
            continue
        out.append(
            PolicyPricedRow(
                row=item.row,
                candidate=item.candidate,
                model_probability=item.probability,
                manual_probability=decision.probability / 100,
                policy=decision.policy,
            )
        )
    return out, skipped


def summarize(rows: list[PolicyPricedRow]) -> dict[str, float]:
    if not rows:
        return {
            "n": 0,
            "official_rbp": 0.0,
            "delta_vs_submitted": 0.0,
            "brier": float("nan"),
            "mean_model": float("nan"),
            "mean_manual": float("nan"),
        }
    return {
        "n": len(rows),
        "official_rbp": sum(official_alt_rbp(row.row, row.manual_probability) for row in rows),
        "delta_vs_submitted": sum(official_delta_rbp(row.row, row.manual_probability) for row in rows),
        "brier": sum(brier(row.manual_probability, row.row.outcome or 0) for row in rows) / len(rows),
        "mean_model": sum(row.model_probability for row in rows) / len(rows),
        "mean_manual": sum(row.manual_probability for row in rows) / len(rows),
    }


def _key(row: PolicyPricedRow) -> tuple[str, str]:
    return row.row.match, row.row.question


def diff_vs_baseline(
    candidate_rows: list[PolicyPricedRow],
    baseline_rows: list[PolicyPricedRow],
) -> dict[str, float]:
    baseline = {_key(row): row for row in baseline_rows}
    paired = [(row, baseline[_key(row)]) for row in candidate_rows if _key(row) in baseline]
    if not paired:
        return {"n": 0, "delta": 0.0, "brier_diff": float("nan")}
    return {
        "n": len(paired),
        "delta": sum(
            official_delta_rbp(row.row, row.manual_probability)
            - official_delta_rbp(base.row, base.manual_probability)
            for row, base in paired
        ),
        "brier_diff": (
            sum(brier(row.manual_probability, row.row.outcome or 0) for row, _base in paired)
            - sum(brier(base.manual_probability, base.row.outcome or 0) for _row, base in paired)
        ) / len(paired),
    }


def bootstrap_diff_vs_baseline(
    candidate_rows: list[PolicyPricedRow],
    baseline_rows: list[PolicyPricedRow],
    *,
    trials: int,
    seed: int,
) -> tuple[int, float, float, float, float]:
    baseline = {_key(row): row for row in baseline_rows}
    by_match: dict[str, list[tuple[PolicyPricedRow, PolicyPricedRow]]] = defaultdict(list)
    for row in candidate_rows:
        base = baseline.get(_key(row))
        if base is not None:
            by_match[row.row.match].append((row, base))
    matches = sorted(by_match)
    if not matches:
        return 0, 0.0, 0.0, 0.0, 0.0
    rng = random.Random(seed)
    draws = []
    for _ in range(trials):
        delta = 0.0
        for match in (rng.choice(matches) for _ in matches):
            for row, base in by_match[match]:
                delta += official_delta_rbp(row.row, row.manual_probability)
                delta -= official_delta_rbp(base.row, base.manual_probability)
        draws.append(delta)
    draws.sort()
    point = sum(
        official_delta_rbp(row.row, row.manual_probability)
        - official_delta_rbp(base.row, base.manual_probability)
        for pairs in by_match.values()
        for row, base in pairs
    )
    return (
        sum(len(pairs) for pairs in by_match.values()),
        point,
        draws[int(0.025 * (trials - 1))],
        draws[int(0.500 * (trials - 1))],
        draws[int(0.975 * (trials - 1))],
    )


def summarize_by(rows: list[PolicyPricedRow], key_fn) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[PolicyPricedRow]] = defaultdict(list)
    for row in rows:
        grouped[key_fn(row)].append(row)
    return {key: summarize(part) for key, part in grouped.items()}


def policy_counts(rows: list[PolicyPricedRow]) -> Counter:
    return Counter(row.policy for row in rows)


def _md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    out.extend("| " + " | ".join(row) + " |" for row in rows)
    return out


def render(
    rows: list[HistoryMarket],
    contexts: dict[str, MatchContext],
    missing_contexts: list[str],
    policy_by_candidate: dict[str, list[PolicyPricedRow]],
    skipped_by_candidate: dict[str, list[str]],
    *,
    now: datetime,
    trials: int,
    seed: int,
) -> str:
    baseline_rows = policy_by_candidate.get(BASELINE_CANDIDATE, [])
    summaries = {name: summarize(priced) for name, priced in policy_by_candidate.items()}
    diffs = {name: diff_vs_baseline(priced, baseline_rows) for name, priced in policy_by_candidate.items()}
    bootstraps = {
        name: bootstrap_diff_vs_baseline(priced, baseline_rows, trials=trials, seed=seed)
        for name, priced in policy_by_candidate.items()
    }

    lines = [
        "# Count Intensity Manual-Policy Replay",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"- settled_count_rows_requested: {len(rows)}",
        f"- matched_match_contexts: {len(contexts)}",
        f"- missing_match_contexts: {len(missing_contexts)}",
        "- odds_source: ESPN/DraftKings historical pickcenter",
        "- model: current CountPricer structure with candidate `TOURNAMENT_INTENSITY` maps",
        "- policy: live `manual_decision()` with each historical row's displayed field average",
        f"- bootstrap: match-level, trials={trials}, seed={seed}, compared to `{BASELINE_CANDIDATE}`",
        "- limitations: historical Polymarket blends, old intraday odds snapshots, and old referee overrides are not reconstructed",
        "",
        "## Candidate Maps",
        "",
    ]
    lines += _md_table(
        ["candidate", "cards", "sot", "corners", "offsides", "fouls"],
        [
            [
                name,
                f"{m['cards']:.2f}",
                f"{m['sot']:.2f}",
                f"{m['corners']:.2f}",
                f"{m['offsides']:.2f}",
                f"{m['fouls']:.2f}",
            ]
            for name, m in candidate_maps().items()
        ],
    )
    lines += ["", "## Overall Manual-Policy Replay", ""]
    lines += _md_table(
        [
            "candidate",
            "n",
            "official_rbp",
            "delta_vs_submitted",
            "delta_vs_current_v4_policy",
            "brier",
            "mean_model",
            "mean_manual",
        ],
        [
            [
                name,
                str(int(s["n"])),
                f"{s['official_rbp']:.2f}",
                f"{s['delta_vs_submitted']:.2f}",
                f"{diffs[name]['delta']:.2f}",
                f"{s['brier']:.4f}",
                f"{s['mean_model']:.3f}",
                f"{s['mean_manual']:.3f}",
            ]
            for name, s in sorted(summaries.items(), key=lambda item: item[1]["official_rbp"], reverse=True)
        ],
    )
    lines += ["", "## Bootstrap Difference Vs Current V4 Policy", ""]
    lines += _md_table(
        ["candidate", "paired_n", "point_delta", "ci_2.5", "ci_50", "ci_97.5"],
        [
            [
                name,
                str(n),
                f"{point:.2f}",
                f"{lo:.2f}",
                f"{mid:.2f}",
                f"{hi:.2f}",
            ]
            for name, (n, point, lo, mid, hi) in bootstraps.items()
        ],
    )

    stat_names = sorted({row.row.stat or "none" for priced in policy_by_candidate.values() for row in priced})
    by_stat = {name: summarize_by(priced, lambda row: row.row.stat or "none") for name, priced in policy_by_candidate.items()}
    lines += ["", "## Manual-Policy Replay By Stat", ""]
    stat_headers = ["stat"]
    for candidate in policy_by_candidate:
        stat_headers += [f"{candidate}_n", f"{candidate}_brier", f"{candidate}_delta"]
    stat_rows = []
    for stat in stat_names:
        row = [stat]
        for candidate in policy_by_candidate:
            s = by_stat[candidate].get(stat, {"n": 0, "brier": float("nan"), "delta_vs_submitted": 0.0})
            row += [str(int(s["n"])), f"{s['brier']:.4f}", f"{s['delta_vs_submitted']:.2f}"]
        stat_rows.append(row)
    lines += _md_table(stat_headers, stat_rows)

    family_names = sorted({row.row.famkey for priced in policy_by_candidate.values() for row in priced})
    by_family = {name: summarize_by(priced, lambda row: row.row.famkey) for name, priced in policy_by_candidate.items()}
    lines += ["", "## Manual-Policy Replay By Family", ""]
    family_headers = ["family"]
    for candidate in policy_by_candidate:
        family_headers += [f"{candidate}_n", f"{candidate}_brier", f"{candidate}_delta"]
    family_rows = []
    for family in family_names:
        row = [family]
        for candidate in policy_by_candidate:
            s = by_family[candidate].get(family, {"n": 0, "brier": float("nan"), "delta_vs_submitted": 0.0})
            row += [str(int(s["n"])), f"{s['brier']:.4f}", f"{s['delta_vs_submitted']:.2f}"]
        family_rows.append(row)
    lines += _md_table(family_headers, family_rows)

    lines += ["", "## Policy Mix", ""]
    mix_headers = ["candidate", "policy", "rows"]
    mix_rows = []
    for candidate, priced in policy_by_candidate.items():
        for policy, count in policy_counts(priced).most_common():
            mix_rows.append([candidate, policy, str(count)])
    lines += _md_table(mix_headers, mix_rows)

    if missing_contexts:
        lines += [
            "",
            "## Missing Match Contexts",
            "",
            *[f"- {item}" for item in missing_contexts[:25]],
        ]
    skipped = sorted({item for values in skipped_by_candidate.values() for item in values})
    if skipped:
        lines += [
            "",
            "## Skipped Rows",
            "",
            *[f"- {item}" for item in skipped[:25]],
        ]

    current = summaries.get(BASELINE_CANDIDATE, {"official_rbp": 0.0})
    best_name, best_summary = max(summaries.items(), key=lambda item: item[1]["official_rbp"])
    best_boot = bootstraps.get(best_name, (0, 0.0, 0.0, 0.0, 0.0))
    promote = (
        best_name != BASELINE_CANDIDATE
        and best_summary["official_rbp"] > current["official_rbp"]
        and best_boot[2] > 0
    )
    lines += [
        "",
        "## Interpretation",
        "",
        f"- Best manual-policy candidate: `{best_name}`.",
        f"- Promotion gate: best candidate must beat `{BASELINE_CANDIDATE}` overall and have positive 2.5% match-bootstrap difference.",
        f"- Promotion decision: {'promote challenger' if promote else 'keep current_v4'}.",
        "- This field-policy replay is historical/diagnostic only for pre-lock manual work because SportsPredict field averages are unavailable before prediction lock.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only settlement for older V1 history rows")
    ap.add_argument("--start", default="2026-06-11", help="first ESPN scoreboard date, YYYY-MM-DD")
    ap.add_argument("--end", default="2026-06-28", help="last ESPN scoreboard date, YYYY-MM-DD")
    ap.add_argument("--trials", type=int, default=1000, help="match-bootstrap trials")
    ap.add_argument("--seed", type=int, default=20260628)
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = load_count_rows(sports_predict=args.sports_predict)
    contexts, missing = build_contexts(
        [row.match for row in rows],
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
    )

    policy_by_candidate: dict[str, list[PolicyPricedRow]] = {}
    skipped_by_candidate: dict[str, list[str]] = {}
    for name, intensity in candidate_maps().items():
        raw_priced, raw_skipped = price_rows(rows, contexts, intensity=intensity, candidate=name)
        policy_priced, policy_skipped = apply_manual_policy(raw_priced)
        policy_by_candidate[name] = policy_priced
        skipped_by_candidate[name] = raw_skipped + policy_skipped

    text = render(
        rows,
        contexts,
        missing,
        policy_by_candidate,
        skipped_by_candidate,
        now=datetime.now(timezone.utc),
        trials=args.trials,
        seed=args.seed,
    )
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
