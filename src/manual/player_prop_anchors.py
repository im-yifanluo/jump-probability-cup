"""Current player-prop market anchors for manual pricing.

These anchors are intentionally exact-match and short-lived. They are for
current opponent-specific prop odds, not reusable player priors.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PlayerPropAnchor:
    probability: float
    source: str
    as_of: str


@dataclass(frozen=True)
class PlayerPropAnchors:
    by_market_id: dict[str, PlayerPropAnchor]
    by_match_question: dict[tuple[str, str], PlayerPropAnchor]
    row_count: int

    @classmethod
    def empty(cls) -> "PlayerPropAnchors":
        return cls({}, {}, 0)


@dataclass(frozen=True)
class PlayerPropAnchorCsvRow:
    line_no: int
    market_id: str
    match: str
    question: str
    probability: float | None
    source: str
    as_of: str
    status: str
    error: str = ""


@dataclass(frozen=True)
class PlayerPropAnchorInspection:
    rows: tuple[PlayerPropAnchorCsvRow, ...]

    @property
    def usable(self) -> tuple[PlayerPropAnchorCsvRow, ...]:
        return tuple(row for row in self.rows if row.status == "usable")

    @property
    def blank(self) -> tuple[PlayerPropAnchorCsvRow, ...]:
        return tuple(row for row in self.rows if row.status == "blank")

    @property
    def invalid(self) -> tuple[PlayerPropAnchorCsvRow, ...]:
        return tuple(row for row in self.rows if row.status == "invalid")


def american_to_probability(odds: int | float) -> float:
    odds = float(odds)
    if odds == 0:
        raise ValueError("American odds cannot be zero")
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return -odds / (-odds + 100.0)


def devig_two_way(yes_american: int | float, no_american: int | float) -> float:
    yes = american_to_probability(yes_american)
    no = american_to_probability(no_american)
    total = yes + no
    if total <= 0:
        raise ValueError("invalid two-way odds")
    return yes / total


def parse_probability(raw: str) -> float:
    value = raw.strip().rstrip("%").strip()
    if not value:
        raise ValueError("empty probability")
    parsed = float(value)
    if parsed <= 0:
        raise ValueError(f"probability must be positive: {raw!r}")
    if parsed <= 1:
        return parsed
    if parsed <= 100:
        return parsed / 100.0
    raise ValueError(f"probability must be <= 100%: {raw!r}")


def inspect_player_prop_anchor_csv(path: str | None) -> PlayerPropAnchorInspection:
    """Inspect a possibly incomplete player-prop anchor CSV.

    Unlike `read_player_prop_anchor_csv`, this allows blank probability/odds
    cells so the generated template can be audited while it is still being
    filled.
    """

    if not path:
        return PlayerPropAnchorInspection(())

    out: list[PlayerPropAnchorCsvRow] = []
    seen_market_ids: set[str] = set()
    seen_match_questions: set[tuple[str, str]] = set()
    with Path(path).open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        headers = set(reader.fieldnames or [])
        has_probability = bool({"probability", "prob", "market_probability"} & headers)
        has_two_way_odds = {"yes_american", "no_american"} <= headers
        if not (has_probability or has_two_way_odds):
            raise ValueError(
                "player prop CSV must include probability/prob/market_probability "
                "or both yes_american and no_american"
            )
        for line_no, row in enumerate(reader, start=2):
            market_id = (row.get("market_id") or row.get("id") or "").strip()
            match = (row.get("match") or row.get("match_name") or "").strip()
            question = (row.get("question") or "").strip()
            source = (row.get("prop_source") or row.get("source") or "").strip()
            as_of = (row.get("as_of") or "").strip()
            error = ""
            probability: float | None = None

            if not market_id and not (match and question):
                out.append(
                    PlayerPropAnchorCsvRow(
                        line_no, market_id, match, question, None, source, as_of,
                        "invalid", "needs market_id or match+question",
                    )
                )
                continue
            if market_id:
                if market_id in seen_market_ids:
                    error = f"duplicate market_id: {market_id}"
                seen_market_ids.add(market_id)
            if match and question:
                key = (match, question)
                if key in seen_match_questions:
                    error = f"duplicate match+question: {match} / {question}"
                seen_match_questions.add(key)

            raw_probability = (
                row.get("probability")
                or row.get("prob")
                or row.get("market_probability")
                or ""
            ).strip()
            yes = (row.get("yes_american") or "").strip()
            no = (row.get("no_american") or "").strip()
            if error:
                status = "invalid"
            elif raw_probability:
                try:
                    probability = parse_probability(raw_probability)
                    status = "usable"
                except ValueError as exc:
                    status = "invalid"
                    error = str(exc)
            elif yes or no:
                if not yes or not no:
                    status = "invalid"
                    error = "needs both yes_american and no_american"
                else:
                    try:
                        probability = devig_two_way(float(yes), float(no))
                        status = "usable"
                    except ValueError as exc:
                        status = "invalid"
                        error = str(exc)
            else:
                status = "blank"

            out.append(
                PlayerPropAnchorCsvRow(
                    line_no, market_id, match, question, probability, source, as_of, status, error
                )
            )
    return PlayerPropAnchorInspection(tuple(out))


def read_player_prop_anchor_csv(path: str | None) -> PlayerPropAnchors:
    """Read exact current player-prop anchors.

    Supported columns:

    - `probability`, `prob`, or `market_probability`, as 0-1, 0-100, or "38%".
    - or two-sided `yes_american` and `no_american`, de-vigged proportionally.
    - keyed by `market_id`/`id`, or by both `match`/`match_name` and `question`.
    - optional `source` and `as_of` audit columns.
    """

    if not path:
        return PlayerPropAnchors.empty()

    by_market_id: dict[str, PlayerPropAnchor] = {}
    by_match_question: dict[tuple[str, str], PlayerPropAnchor] = {}
    row_count = 0
    inspection = inspect_player_prop_anchor_csv(path)
    for row in inspection.rows:
        if row.status != "usable":
            detail = f": {row.error}" if row.error else ""
            raise ValueError(f"player prop CSV line {row.line_no} is {row.status}{detail}")
        anchor = PlayerPropAnchor(
            probability=row.probability or 0.0,
            source=row.source,
            as_of=row.as_of,
        )
        if row.market_id:
            by_market_id[row.market_id] = anchor
        if row.match and row.question:
            by_match_question[(row.match, row.question)] = anchor
        row_count += 1
    return PlayerPropAnchors(by_market_id, by_match_question, row_count)


def anchor_for_market(row, anchors: PlayerPropAnchors) -> PlayerPropAnchor | None:
    if anchors.row_count == 0:
        return None
    market_id = getattr(row, "market_id", "")
    if market_id and market_id in anchors.by_market_id:
        return anchors.by_market_id[market_id]
    return anchors.by_match_question.get((getattr(row, "match_name", ""), getattr(row, "question", "")))
