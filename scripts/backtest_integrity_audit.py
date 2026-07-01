"""Audit the integrity of `docs/history.md` strategy-backtest inputs.

This read-only diagnostic focuses on settlement correctness.  Version 2 history
rows carry explicit outcomes and official RBP.  Version 1 rows only carry
`If Yes`/`If No`; with SportsPredict `/results`, they can be settled only when
the repeated question text maps to a unique inferred outcome.

The script never reads `/predictions` and never submits.

Usage:
    uv run --with requests python scripts/backtest_integrity_audit.py --sports-predict --write
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from backtest.history import (  # noqa: E402
    HistoryMarket,
    ambiguous_outcome_questions,
    exact_rows,
    load_history,
    result_outcomes_by_question,
    result_outcome,
    settle_v1_rows,
    uninferable_result_questions,
    uninferable_result_rows,
    unique_outcomes_by_question,
)
from manual.anchors import sp_get, sports_predict_key  # noqa: E402


OUT = ROOT / "data" / "logs" / "backtest_integrity_audit.md"
HISTORY = ROOT / "docs" / "history.md"


def fetch_results() -> list[dict]:
    key = sports_predict_key()
    if not key:
        raise SystemExit("No SPORTSPREDICT_KEY_1/SPORTSPREDICT_KEY found for --sports-predict")
    events = sp_get("/events?limit=100", key)
    event = next(e for e in events if "probability cup" in e.get("title", "").lower())
    lobby = sp_get(f"/lobbies?event_id={event['id']}", key)[0]
    return sp_get(f"/results?lobby_id={lobby['id']}", key)


def unsafe_last_outcomes_by_question(results: list[dict]) -> dict[str, int]:
    """Reproduce the old question-only overwrite behavior for comparison."""

    out: dict[str, int] = {}
    for result in results:
        question = str(result.get("question", "")).strip()
        outcome = result_outcome(result)
        if question and outcome is not None:
            out[question] = outcome
    return out


def classify_v1(rows: list[HistoryMarket], results: list[dict]) -> dict[str, list[HistoryMarket]]:
    outcomes = result_outcomes_by_question(results)
    uninferable_questions = uninferable_result_questions(results)
    buckets: dict[str, list[HistoryMarket]] = {
        "safe_unique": [],
        "ambiguous": [],
        "uninferable": [],
        "missing": [],
    }
    for row in rows:
        if row.version != 1:
            continue
        question = row.question.strip()
        vals = outcomes.get(question)
        if vals is None:
            if question in uninferable_questions:
                buckets["uninferable"].append(row)
            else:
                buckets["missing"].append(row)
        elif len(vals) == 1:
            if question in uninferable_questions:
                buckets["uninferable"].append(row)
            else:
                buckets["safe_unique"].append(row)
        else:
            buckets["ambiguous"].append(row)
    return buckets


def duplicate_questions(rows: list[HistoryMarket]) -> list[tuple[str, int, int]]:
    by_question: dict[str, list[HistoryMarket]] = defaultdict(list)
    for row in rows:
        by_question[row.question.strip()].append(row)
    out = []
    for question, part in by_question.items():
        if len(part) > 1:
            out.append((question, len(part), len({row.match for row in part})))
    return sorted(out, key=lambda item: (-item[1], item[0]))


def family_counts(rows: list[HistoryMarket]) -> list[tuple[str, int]]:
    counts = Counter(row.famkey for row in rows)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def render(rows: list[HistoryMarket], results: list[dict], *, now: datetime) -> str:
    safe_outcomes = unique_outcomes_by_question(results)
    unsafe_outcomes = unsafe_last_outcomes_by_question(results)
    safe_rows = settle_v1_rows(rows, safe_outcomes)
    unsafe_rows = settle_v1_rows(rows, unsafe_outcomes)
    safe_exact = exact_rows(safe_rows)
    unsafe_exact = exact_rows(unsafe_rows)
    v1 = [row for row in rows if row.version == 1]
    v2 = [row for row in rows if row.version == 2]
    buckets = classify_v1(rows, results)
    ambiguous_questions = ambiguous_outcome_questions(results)
    uninferable_rows = uninferable_result_rows(results)
    uninferable_questions = uninferable_result_questions(results)
    duplicates = duplicate_questions(rows)

    lines = [
        "# Backtest Integrity Audit",
        "",
        "Read-only analysis. `/predictions` was not read and no submissions were made.",
        "",
        f"- run_utc: {now.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        f"- history_rows: {len(rows)}",
        f"- history_v1_rows: {len(v1)}",
        f"- history_v2_rows: {len(v2)}",
        f"- unparsed_rows: {sum(row.family == 'UNPARSED' for row in rows)}",
        f"- sports_predict_results_rows: {len(results)}",
        f"- duplicate_history_question_texts: {len(duplicates)}",
        f"- ambiguous_result_question_texts: {len(ambiguous_questions)}",
        f"- uninferable_result_rows: {len(uninferable_rows)}",
        f"- uninferable_result_question_texts: {len(uninferable_questions)}",
        f"- v1_safe_unique_settled_rows: {len(buckets['safe_unique'])}",
        f"- v1_ambiguous_left_unsettled_rows: {len(buckets['ambiguous'])}",
        f"- v1_uninferable_left_unsettled_rows: {len(buckets['uninferable'])}",
        f"- v1_missing_result_rows: {len(buckets['missing'])}",
        f"- safe_exact_rows_for_strategy_scoring: {len(safe_exact)}",
        f"- unsafe_question_overwrite_exact_rows: {len(unsafe_exact)}",
        "",
        "## Settlement Rule",
        "",
        "- V2 rows are exact from `docs/history.md`: explicit result, outcome, field, submitted probability, and official RBP.",
        "- V1 rows are settled from SportsPredict `/results` only when every result row with the same question text implies the same binary outcome.",
        "- Rows whose submitted probability and Brier score are equally compatible with both outcomes, such as 50% with 0.25 Brier, are uninferable and cannot settle V1 history.",
        "- If the same question text appears in `/results` with both outcomes, the V1 row remains unsettled because `/results` does not expose historical match names and `docs/history.md` V1 rows do not expose market IDs.",
        "",
        "## V1 Settlement Buckets",
        "",
        "| bucket | rows |",
        "| --- | ---: |",
        f"| safe unique outcome | {len(buckets['safe_unique'])} |",
        f"| ambiguous outcome, excluded | {len(buckets['ambiguous'])} |",
        f"| uninferable from Brier, excluded | {len(buckets['uninferable'])} |",
        f"| missing result, excluded | {len(buckets['missing'])} |",
        "",
        "## Safe Exact Rows By Family",
        "",
        "| family | rows |",
        "| --- | ---: |",
    ]
    for family, count in family_counts(safe_exact):
        lines.append(f"| {family} | {count} |")

    if buckets["ambiguous"]:
        lines.extend(
            [
                "",
                "## Ambiguous V1 Rows Excluded",
                "",
                "| match | question | result_outcomes_seen |",
                "| --- | --- | --- |",
            ]
        )
        for row in buckets["ambiguous"]:
            outcomes = ",".join(str(v) for v in sorted(ambiguous_questions.get(row.question.strip(), set())))
            lines.append(f"| {row.match} | {row.question} | {outcomes} |")

    if buckets["uninferable"]:
        lines.extend(
            [
                "",
                "## Uninferable V1 Rows Excluded",
                "",
                "| match | question |",
                "| --- | --- |",
            ]
        )
        for row in buckets["uninferable"]:
            lines.append(f"| {row.match} | {row.question} |")

    if uninferable_rows:
        lines.extend(
            [
                "",
                "## Uninferable SportsPredict Result Rows",
                "",
                "| question | submitted_probability | brier_score |",
                "| --- | ---: | ---: |",
            ]
        )
        for result in uninferable_rows[:25]:
            probability = result.get("probability_submitted", result.get("probability", ""))
            lines.append(f"| {str(result.get('question', '')).strip()} | {probability} | {result.get('brier_score', '')} |")

    if duplicates:
        lines.extend(
            [
                "",
                "## Most-Repeated History Question Texts",
                "",
                "| question | rows | matches |",
                "| --- | ---: | ---: |",
            ]
        )
        for question, count, matches in duplicates[:25]:
            lines.append(f"| {question} | {count} | {matches} |")

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- The old question-only overwrite join would have scored more rows, but it was not exact for repeated V1 questions.",
            "- Promoted strategy diagnostics should use the safe exact row set unless a future API endpoint exposes settled market IDs with match metadata.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sports-predict", action="store_true", help="GET-only SportsPredict result audit")
    ap.add_argument("--write", action="store_true", help=f"write {OUT}")
    args = ap.parse_args()

    rows = load_history(HISTORY)
    if not args.sports_predict:
        raise SystemExit("--sports-predict is required because V1 settlement integrity depends on /results")
    text = render(rows, fetch_results(), now=datetime.now(timezone.utc))
    print(text)
    if args.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
