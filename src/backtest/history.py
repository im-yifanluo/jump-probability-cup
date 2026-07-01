"""Backtest helpers for SportsPredict history exports.

The site shows official per-market RBP for settled markets.  That RBP includes
the field's variance term, which we cannot reconstruct from just the average
crowd probability.  For strategy comparisons, however, the unknown crowd term
cancels exactly:

    alt_rbp = official_rbp + weight * 100 * (brier_current - brier_alt)

So this module treats the displayed official RBP as the anchor and evaluates
candidate probabilities by exact Brier deltas.  It deliberately has no pandas
dependency so it can run in a bare checkout.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import re
from pathlib import Path
from typing import Callable, Iterable


@dataclass(frozen=True)
class HistoryMarket:
    version: int
    order: int
    match: str
    question: str
    you: float
    crowd: float
    outcome: int | None
    official_rbp: float | None
    family: str = "UNPARSED"
    stat: str = ""
    period: str = "match"
    if_yes: float | None = None
    if_no: float | None = None

    @property
    def famkey(self) -> str:
        out = self.family
        if self.stat:
            out += f"/{self.stat}"
        if self.period and self.period != "match":
            out += f"/{self.period}"
        return out

    @property
    def settled(self) -> bool:
        return self.outcome is not None


V1_ROW = re.compile(
    r"^(?P<q>.*?)\t(?P<you>\d+)%\t(?P<crowd>\d+)%\t"
    r"(?P<if_yes>[-+]?\d+(?:\.\d+)?)\t(?P<if_no>[-+]?\d+(?:\.\d+)?)\t(?P<swing>\d+)\s*$"
)


def _percent_after(label: str, lines: list[str], start: int) -> float | None:
    for i in range(start, len(lines) - 1):
        if lines[i].strip() == label:
            m = re.search(r"(\d+)%", lines[i + 1])
            return int(m.group(1)) / 100 if m else None
    return None


def _number_after(label: str, lines: list[str], start: int) -> float | None:
    for i in range(start, len(lines) - 1):
        if lines[i].strip() == label:
            m = re.search(r"[-+]?\d+(?:\.\d+)?", lines[i + 1])
            return float(m.group(0)) if m else None
    return None


def _annotate(question: str) -> tuple[str, str, str]:
    try:
        from questions.parser import parse
        from questions.teams import ALL_TEAM_NAMES

        spec = parse(question, teams=ALL_TEAM_NAMES)
        return spec.family, spec.stat or "", spec.period
    except Exception:
        return "UNPARSED", "", "match"


def parse_history(text: str) -> list[HistoryMarket]:
    """Parse both history formats.

    Version 2 blocks carry explicit outcomes and official per-market RBP.
    Version 1 tabular blocks are retained but unsettled unless the caller joins
    an outcome source later; they are not used in exact strategy scoring.
    """
    v1_text, _, v2_text = text.partition("## Version 2")
    rows: list[HistoryMarket] = []
    order = 0

    current_match: str | None = None
    for line in v1_text.splitlines():
        header = re.match(r"^###\s+(.*?)\s+is live", line)
        if header:
            current_match = header.group(1).strip()
            order += 1
            continue
        m = V1_ROW.match(line)
        if m and current_match:
            q = m.group("q").strip()
            fam, stat, period = _annotate(q)
            rows.append(
                HistoryMarket(
                    version=1,
                    order=order,
                    match=current_match,
                    question=q,
                    you=int(m.group("you")) / 100,
                    crowd=int(m.group("crowd")) / 100,
                    outcome=None,
                    official_rbp=None,
                    family=fam,
                    stat=stat,
                    period=period,
                    if_yes=float(m.group("if_yes")),
                    if_no=float(m.group("if_no")),
                )
            )

    for block in re.split(r"\n###\s+", v2_text):
        block = block.strip()
        if not block:
            continue
        lines = [ln.rstrip() for ln in block.splitlines()]
        match = lines[0].strip()
        if not match or match.startswith("##"):
            continue
        order += 1
        q_indices = [i for i, ln in enumerate(lines) if re.match(r"^Q\d+\s*$", ln.strip())]
        for n, start in enumerate(q_indices):
            end = q_indices[n + 1] if n + 1 < len(q_indices) else len(lines)
            chunk = lines[start:end]
            result_i = next((i for i, ln in enumerate(chunk) if ln.startswith("RESULT:")), None)
            you_i = next((i for i, ln in enumerate(chunk) if ln.strip() == "YOU"), None)
            if result_i is None or you_i is None or you_i <= result_i + 1:
                continue
            result = chunk[result_i].split(":", 1)[1].strip().upper()
            question = " ".join(ln.strip() for ln in chunk[result_i + 1 : you_i] if ln.strip())
            you = _percent_after("YOU", chunk, you_i)
            crowd = _percent_after("CROWD", chunk, you_i)
            outcome_pct = _percent_after("OUTCOME", chunk, you_i)
            rbp = _number_after("RBP", chunk, you_i)
            if not question or you is None or crowd is None:
                continue
            outcome = int(outcome_pct == 1.0) if outcome_pct is not None else int(result == "YES")
            fam, stat, period = _annotate(question)
            rows.append(
                HistoryMarket(
                    version=2,
                    order=order,
                    match=match,
                    question=question,
                    you=you,
                    crowd=crowd,
                    outcome=outcome,
                    official_rbp=rbp,
                    family=fam,
                    stat=stat,
                    period=period,
                )
            )
    return rows


def result_outcome(result: dict, *, tolerance: float = 1e-12) -> int | None:
    """Infer a settled binary outcome from a SportsPredict `/results` row.

    The result row contains the submitted probability and realized Brier score,
    not a direct `outcome` field.  For a binary market, whichever candidate
    outcome has Brier closer to the displayed score is the realized outcome.
    If both outcomes are equally consistent, the row is not inferable from the
    available fields and returns `None`.
    """

    p = float(result.get("probability_submitted", result.get("probability", 0.5)))
    if p > 1:
        p /= 100
    score = float(result["brier_score"])
    no_diff = abs(score - brier(p, 0))
    yes_diff = abs(score - brier(p, 1))
    if abs(no_diff - yes_diff) <= tolerance:
        return None
    return 0 if no_diff < yes_diff else 1


def uninferable_result_questions(results: Iterable[dict]) -> set[str]:
    questions: set[str] = set()
    for result in results:
        question = str(result.get("question", "")).strip()
        if question and result_outcome(result) is None:
            questions.add(question)
    return questions


def uninferable_result_rows(results: Iterable[dict]) -> list[dict]:
    return [
        result
        for result in results
        if str(result.get("question", "")).strip() and result_outcome(result) is None
    ]


def result_outcomes_by_question(results: Iterable[dict]) -> dict[str, set[int]]:
    grouped: dict[str, set[int]] = {}
    for result in results:
        question = str(result.get("question", "")).strip()
        if not question:
            continue
        outcome = result_outcome(result)
        if outcome is None:
            continue
        grouped.setdefault(question, set()).add(outcome)
    return grouped


def unique_outcomes_by_question(results: Iterable[dict]) -> dict[str, int]:
    """Return only question texts whose `/results` rows imply one outcome.

    SportsPredict `/results` exposes `market_id`, but not the historical match
    name, while `docs/history.md` V1 rows do not have market IDs.  Many question
    texts repeat across matches, so question-only settlement is exact only when
    every result row for that question has the same inferred outcome.  Ambiguous
    question texts are deliberately omitted and remain unsettled.
    """

    grouped = result_outcomes_by_question(results)
    uninferable_questions = uninferable_result_questions(results)
    return {
        question: next(iter(outcomes))
        for question, outcomes in grouped.items()
        if len(outcomes) == 1 and question not in uninferable_questions
    }


def ambiguous_outcome_questions(results: Iterable[dict]) -> dict[str, set[int]]:
    grouped = result_outcomes_by_question(results)
    return {question: outcomes for question, outcomes in grouped.items() if len(outcomes) > 1}


def settle_v1_rows(rows: Iterable[HistoryMarket], outcomes_by_question: dict[str, int | None]) -> list[HistoryMarket]:
    """Fill V1 outcomes/RBP from an external outcome map.

    V1 history rows show `If Yes` and `If No` official RBP values but not the
    realized outcome.  Once the outcome is known, the official row score is
    exact: `if_yes` for outcome 1, `if_no` for outcome 0.
    """
    settled = []
    for row in rows:
        if row.outcome is not None or row.version != 1:
            settled.append(row)
            continue
        outcome = outcomes_by_question.get(row.question.strip())
        if outcome is None or row.if_yes is None or row.if_no is None:
            settled.append(row)
            continue
        settled.append(replace(row, outcome=int(outcome), official_rbp=row.if_yes if outcome else row.if_no))
    return settled


def brier(p: float, outcome: int) -> float:
    return (p - outcome) ** 2


def skill_rbp(p: float, crowd: float, outcome: int, weight: float = 1.0) -> float:
    return weight * 100.0 * (brier(crowd, outcome) - brier(p, outcome))


def official_delta_rbp(row: HistoryMarket, p_alt: float, weight: float = 1.0) -> float:
    if row.outcome is None:
        raise ValueError("cannot score an unsettled market")
    return weight * 100.0 * (brier(row.you, row.outcome) - brier(p_alt, row.outcome))


def official_alt_rbp(row: HistoryMarket, p_alt: float, weight: float = 1.0) -> float:
    if row.official_rbp is None:
        raise ValueError("official_rbp is required for exact official alternate scoring")
    return row.official_rbp + official_delta_rbp(row, p_alt, weight)


def clip_prob(p: float) -> float:
    return min(0.99, max(0.01, p))


Strategy = Callable[[HistoryMarket], float]


def current_strategy(row: HistoryMarket) -> float:
    return row.you


def crowd_strategy(row: HistoryMarket) -> float:
    return row.crowd


def flat50_strategy(row: HistoryMarket) -> float:
    return 0.50


def shrink_to_crowd(k: float) -> Strategy:
    def _strategy(row: HistoryMarket) -> float:
        return clip_prob(row.crowd + k * (row.you - row.crowd))

    return _strategy


def cap_deviation(max_abs_dev: float) -> Strategy:
    """Keep the model side, but limit distance from the field average."""

    def _strategy(row: HistoryMarket) -> float:
        d = row.you - row.crowd
        if abs(d) <= max_abs_dev:
            return clip_prob(row.you)
        return clip_prob(row.crowd + math.copysign(max_abs_dev, d))

    return _strategy


def asymmetric_field_policy(
    *,
    above_k: float,
    below_k: float,
    max_abs_dev: float | None = None,
    neutral_width: float = 0.0,
    neutral_k: float = 0.0,
) -> Strategy:
    """Transform model-vs-field deviations for manual entry.

    `above_k` applies when the model is above the field average, `below_k`
    applies when the model is below it.  The optional neutral band is useful
    when tiny differences are mostly noise and should be blended back toward
    the field average.
    """

    def _strategy(row: HistoryMarket) -> float:
        d = row.you - row.crowd
        if abs(d) < neutral_width:
            k = neutral_k
        elif d > 0:
            k = above_k
        else:
            k = below_k
        adjusted = k * d
        if max_abs_dev is not None and abs(adjusted) > max_abs_dev:
            adjusted = math.copysign(max_abs_dev, adjusted)
        return clip_prob(row.crowd + adjusted)

    return _strategy


def logit_recalibration(alpha: float, beta: float) -> Strategy:
    def _strategy(row: HistoryMarket) -> float:
        p = clip_prob(row.you)
        z = math.log(p / (1 - p))
        return 1 / (1 + math.exp(-(alpha + beta * z)))

    return _strategy


def exact_rows(rows: Iterable[HistoryMarket]) -> list[HistoryMarket]:
    return [r for r in rows if r.outcome is not None and r.official_rbp is not None]


def evaluate(rows: Iterable[HistoryMarket], strategy: Strategy) -> dict[str, float]:
    settled = exact_rows(rows)
    if not settled:
        return {"n": 0, "official_rbp": 0.0, "delta": 0.0, "brier": float("nan")}
    alt = [clip_prob(strategy(r)) for r in settled]
    rbp = [official_alt_rbp(r, p) for r, p in zip(settled, alt)]
    delta = [official_delta_rbp(r, p) for r, p in zip(settled, alt)]
    br = [brier(p, r.outcome or 0) for r, p in zip(settled, alt)]
    return {
        "n": len(settled),
        "official_rbp": sum(rbp),
        "delta": sum(delta),
        "brier": sum(br) / len(br),
    }


def group_by_fam(rows: Iterable[HistoryMarket]) -> dict[str, list[HistoryMarket]]:
    out: dict[str, list[HistoryMarket]] = {}
    for row in rows:
        out.setdefault(row.famkey, []).append(row)
    return out


def load_history(path: str | Path) -> list[HistoryMarket]:
    return parse_history(Path(path).read_text())
