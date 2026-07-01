"""Audit official settled SportsPredict special-market outcomes.

The main special-market validation uses StatsBomb tournament data because
`docs/history.md` has no exported special/timing rows with field average and
RBP. This read-only audit checks whether `/results` now contains settled
special-market questions and compares their inferred outcomes with the
StatsBomb special priors.

Important limitation: `/results` contains submitted probabilities and Brier
scores, but not the field average, match name, or official RBP. This script uses
submitted probability only to infer the binary outcome from the Brier score; it
does not evaluate submitted/bot performance and does not read `/predictions`.

Usage:
    uv run --with requests --with numpy --with scipy python scripts/special_official_results_audit.py --sports-predict --write
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from backtest.history import brier, result_outcome  # noqa: E402
from leaderboard_tail_risk import special_proxy_probability  # noqa: E402
from manual.anchors import sp_get, sports_predict_key  # noqa: E402
from manual.policy import is_special_market  # noqa: E402
from questions.parser import parse  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402
from special_market_backtest import rows as statsbomb_rows, wilson_interval  # noqa: E402


OUT = ROOT / "data" / "logs" / "special_official_results_audit.md"

SPECIAL_LIKE_PHRASES = (
    "hydration",
    "first-half stoppage",
    "second-half stoppage",
    "substitute score",
    "substitution be made before halftime",
    "any player score more than",
    "any player record",
    "total shots (on and off target)",
    "corner kicks be taken before the first hydration break",
)


@dataclass(frozen=True)
class OfficialSpecialResult:
    market_id: str
    question: str
    family: str
    stat: str
    period: str
    outcome: int
    prior: float

    @property
    def famkey(self) -> str:
        out = self.family
        if self.stat:
            out += f"/{self.stat}"
        if self.period and self.period != "match":
            out += f"/{self.period}"
        return out


def read_results() -> list[dict]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("No SPORTSPREDICT_KEY_1/SPORTSPREDICT_KEY found for --sports-predict")
    events = sp_get("/events?limit=100", key)
    event = next(e for e in events if "probability cup" in e.get("title", "").lower())
    lobby = sp_get(f"/lobbies?event_id={event['id']}", key)[0]
    return sp_get(f"/results?lobby_id={lobby['id']}", key)


def special_prior(question: str, data: list[dict]) -> float | None:
    try:
        return float(special_proxy_probability(data, question))
    except Exception:
        return None


def is_special_like_question(question: str) -> bool:
    q = question.lower()
    return any(phrase in q for phrase in SPECIAL_LIKE_PHRASES)


def special_result_coverage(results: list[dict], data: list[dict]) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Explain whether a low official special sample is real coverage or audit loss."""

    counts = {
        "settled_results_rows": 0,
        "special_like_settled_rows": 0,
        "parsed_special_settled_rows": 0,
        "usable_special_rows": 0,
        "unsupported_special_rows": 0,
        "uninferable_special_rows": 0,
        "parse_error_special_like_rows": 0,
        "special_like_non_special_rows": 0,
    }
    samples: dict[str, list[str]] = {
        "parse_error_special_like": [],
        "unsupported_special": [],
        "uninferable_special": [],
        "special_like_non_special": [],
    }
    for result in results:
        if result.get("market_status") != "settled":
            continue
        counts["settled_results_rows"] += 1
        question = str(result.get("question", "")).strip()
        if not question:
            continue
        special_like = is_special_like_question(question)
        if special_like:
            counts["special_like_settled_rows"] += 1
        try:
            spec = parse(question, teams=ALL_TEAM_NAMES)
        except Exception as exc:
            if special_like:
                counts["parse_error_special_like_rows"] += 1
                if len(samples["parse_error_special_like"]) < 10:
                    samples["parse_error_special_like"].append(f"{question} :: {exc}")
            continue
        if not is_special_market(spec):
            if special_like:
                counts["special_like_non_special_rows"] += 1
                if len(samples["special_like_non_special"]) < 10:
                    samples["special_like_non_special"].append(
                        f"{question} :: {spec.family}/{spec.stat or '-'}/{spec.period}"
                    )
            continue
        counts["parsed_special_settled_rows"] += 1
        if special_prior(question, data) is None:
            counts["unsupported_special_rows"] += 1
            if len(samples["unsupported_special"]) < 10:
                samples["unsupported_special"].append(question)
            continue
        if result_outcome(result) is None:
            counts["uninferable_special_rows"] += 1
            if len(samples["uninferable_special"]) < 10:
                samples["uninferable_special"].append(question)
            continue
        counts["usable_special_rows"] += 1
    return counts, samples


def settled_special_results(results: list[dict], data: list[dict]) -> tuple[list[OfficialSpecialResult], list[str]]:
    out: list[OfficialSpecialResult] = []
    skipped: list[str] = []
    for result in results:
        if result.get("market_status") != "settled":
            continue
        question = str(result.get("question", "")).strip()
        if not question:
            continue
        try:
            spec = parse(question, teams=ALL_TEAM_NAMES)
        except Exception as exc:
            skipped.append(f"parse:{question}: {exc}")
            continue
        if not is_special_market(spec):
            continue
        prior = special_prior(question, data)
        if prior is None:
            skipped.append(f"unsupported_special:{question}")
            continue
        outcome = result_outcome(result)
        if outcome is None:
            skipped.append(f"uninferable_outcome:{question}")
            continue
        out.append(
            OfficialSpecialResult(
                market_id=str(result.get("market_id", "")),
                question=question,
                family=spec.family,
                stat=spec.stat or "",
                period=spec.period,
                outcome=outcome,
                prior=prior,
            )
        )
    return out, skipped


def grouped(rows: list[OfficialSpecialResult], key_fn) -> list[dict]:
    groups: dict[str, list[OfficialSpecialResult]] = defaultdict(list)
    for row in rows:
        groups[key_fn(row)].append(row)
    out: list[dict] = []
    for key, part in sorted(groups.items()):
        successes = sum(row.outcome for row in part)
        n = len(part)
        low, high = wilson_interval(successes, n)
        brier_prior = sum(brier(row.prior, row.outcome) for row in part) / n
        brier_50 = sum(brier(0.5, row.outcome) for row in part) / n
        out.append(
            {
                "group": key,
                "n": n,
                "successes": successes,
                "hit": successes / n,
                "wilson_low": low,
                "wilson_high": high,
                "prior_mean": sum(row.prior for row in part) / n,
                "brier_prior": brier_prior,
                "brier_50": brier_50,
                "edge_vs_50": brier_50 - brier_prior,
            }
        )
    return sorted(out, key=lambda row: (str(row["group"])))


def md_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def render(results: list[dict], rows: list[OfficialSpecialResult], skipped: list[str], data: list[dict]) -> str:
    coverage, samples = special_result_coverage(results, data)
    lines = [
        "# Official Special Results Audit",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- sports_predict_results_rows: {len(results)}",
        f"- settled_special_rows: {len(rows)}",
        f"- settled_special_questions: {len({row.question for row in rows})}",
        f"- statsbomb_proxy_matches: {len(data)}",
        "- limitation: `/results` has no field average, match name, or official RBP; this is an outcome-rate check, not a full manual-policy backtest",
        "- outcome_inference: binary result inferred from settled Brier score; submitted probabilities are not scored or compared",
        "",
        "## Coverage Diagnostics",
        "",
        f"- settled_results_rows: {coverage['settled_results_rows']}",
        f"- special_like_settled_rows: {coverage['special_like_settled_rows']}",
        f"- parsed_special_settled_rows: {coverage['parsed_special_settled_rows']}",
        f"- usable_special_rows: {coverage['usable_special_rows']}",
        f"- unsupported_special_rows: {coverage['unsupported_special_rows']}",
        f"- uninferable_special_rows: {coverage['uninferable_special_rows']}",
        f"- parse_error_special_like_rows: {coverage['parse_error_special_like_rows']}",
        f"- special_like_non_special_rows: {coverage['special_like_non_special_rows']}",
        "",
        "## By Family",
        "",
    ]
    lines += md_table(
        [
            "family",
            "n",
            "successes",
            "hit_rate",
            "wilson_95",
            "prior_mean",
            "brier_prior",
            "brier_50",
            "edge_vs_50",
        ],
        [
            [
                str(row["group"]),
                f"{int(row['n'])}",
                f"{int(row['successes'])}",
                f"{float(row['hit']):.3f}",
                f"{float(row['wilson_low']):.3f}-{float(row['wilson_high']):.3f}",
                f"{float(row['prior_mean']):.3f}",
                f"{float(row['brier_prior']):.4f}",
                f"{float(row['brier_50']):.4f}",
                f"{float(row['edge_vs_50']):+.4f}",
            ]
            for row in grouped(rows, lambda row: row.famkey)
        ],
    )
    lines += ["", "## By Question", ""]
    lines += md_table(
        [
            "question",
            "n",
            "successes",
            "hit_rate",
            "prior_mean",
            "brier_prior",
            "edge_vs_50",
        ],
        [
            [
                str(row["group"]),
                f"{int(row['n'])}",
                f"{int(row['successes'])}",
                f"{float(row['hit']):.3f}",
                f"{float(row['prior_mean']):.3f}",
                f"{float(row['brier_prior']):.4f}",
                f"{float(row['edge_vs_50']):+.4f}",
            ]
            for row in grouped(rows, lambda row: row.question)
        ],
    )
    if skipped:
        lines += [
            "",
            "## Skipped Special-Like Rows",
            "",
            *[f"- {item}" for item in skipped[:50]],
        ]
    for key, title in [
        ("parse_error_special_like", "Special-Like Parse Errors"),
        ("unsupported_special", "Unsupported Parsed Special Rows"),
        ("uninferable_special", "Uninferable Parsed Special Rows"),
        ("special_like_non_special", "Special-Like Non-Special Samples"),
    ]:
        if samples[key]:
            lines += [
                "",
                f"## {title}",
                "",
                *[f"- {item}" for item in samples[key]],
            ]
    lines += [
        "",
        "## Interpretation",
        "",
        "- If this audit has few rows, keep using the StatsBomb special-market backtest and tail-risk proxy as the primary evidence.",
        "- A positive `edge_vs_50` here supports the direction of the StatsBomb prior on official settled questions; it still does not validate field-relative transforms because field/RBP are absent.",
        "- Do not promote or reject `raw_special_model` from this audit alone unless the official sample becomes large enough for stable family-level conclusions.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only /results audit")
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()
    if not args.sports_predict:
        raise SystemExit("--sports-predict is required for official /results audit")

    data = statsbomb_rows()
    results = read_results()
    official_rows, skipped = settled_special_results(results, data)
    text = render(results, official_rows, skipped, data)
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
