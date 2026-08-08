"""Exact Polymarket anchors for non-player manual markets.

The normal goal model fits a coherent score grid from 1X2 and totals.  When
Polymarket exposes the exact same derivative market as a SportsPredict question
with enough market-level volume, that direct price is a stronger current anchor
than re-deriving the probability from less-specific markets.
"""

from __future__ import annotations

from dataclasses import dataclass
import ast
import json
import re
import urllib.parse
import urllib.request

from questions.parser import H1, H2, MATCH, MarketSpec
from questions.teams import names_match, match_teams


POLYMARKET_EVENTS = "https://gamma-api.polymarket.com/events"

DIRECT_OVERRIDE_FAMILIES = {
    "btts",
    "ht_team_winning",
    "ht_tied",
    "team_clean_sheet",
    "team_first_goal_game",
    "team_goals_ge",
    "team_scores_in_half",
    "team_win_by_ge",
    "total_goals",
}


PLAYER_PROP_FAMILIES = {
    "player_scores",
    "player_scores_or_assists",
    "player_saves_ge",
    "player_sot_ge",
}


COUNT_PROP_FAMILIES = {
    "team_count_ge",
    "total_count_ge",
}


@dataclass(frozen=True)
class DirectMarketAnchor:
    probability: float
    source: str
    event_title: str
    market_question: str
    market_volume: float
    outcome: str

    @property
    def note(self) -> str:
        return f"{self.source} vol=${self.market_volume:,.0f} outcome={self.outcome}"


@dataclass(frozen=True)
class PlayerPropCandidate:
    status: str
    reason: str
    probability: float | None
    event_title: str
    market_question: str
    market_volume: float
    expected_prop: str
    actual_prop: str


def fetch_polymarket_events() -> list[dict]:
    events: list[dict] = []
    seen: set[str] = set()
    for params in (
        {"closed": "false", "tag_slug": "soccer", "limit": 100},
        {"closed": "false", "series_slug": "soccer-fifwc", "limit": 100, "offset": 0},
        {"closed": "false", "series_slug": "soccer-fifwc", "limit": 100, "offset": 100},
    ):
        url = f"{POLYMARKET_EVENTS}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": "manual-direct-anchors/1.0"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode())
        for event in data:
            key = str(event.get("id") or event.get("slug") or event.get("title"))
            if key in seen:
                continue
            seen.add(key)
            events.append(event)
    return events


def direct_anchor_for_market(
    match_name: str,
    spec: MarketSpec,
    events: list[dict],
    *,
    min_volume: float,
) -> DirectMarketAnchor | None:
    """Return the best exact Polymarket anchor for a market spec if available."""

    if spec.player or spec.family not in DIRECT_OVERRIDE_FAMILIES:
        return None
    first, second = match_teams(match_name)
    candidates: list[DirectMarketAnchor] = []
    for event in events:
        if not _event_matches(event, first, second):
            continue
        for market in event.get("markets") or []:
            anchor = _anchor_from_market(str(event.get("title") or ""), market, spec, first, second)
            if anchor and anchor.market_volume >= min_volume:
                candidates.append(anchor)
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.market_volume)


def player_prop_anchor_for_market(
    match_name: str,
    spec: MarketSpec,
    events: list[dict],
    *,
    min_volume: float,
) -> DirectMarketAnchor | None:
    """Return an exact Polymarket player-prop anchor if the row is usable.

    Player markets are thinner and more identity-sensitive than match markets,
    so this is deliberately narrower than `direct_anchor_for_market`: exact
    same match, exact player prop family, matching threshold, and a separate
    market-volume gate are required.
    """

    if not spec.player or spec.family not in PLAYER_PROP_FAMILIES:
        return None
    if spec.period != MATCH:
        return None
    first, second = match_teams(match_name)
    candidates: list[DirectMarketAnchor] = []
    for event in events:
        if not _event_matches(event, first, second):
            continue
        for market in event.get("markets") or []:
            anchor = _player_prop_anchor_from_market(str(event.get("title") or ""), market, spec)
            if anchor and anchor.market_volume >= min_volume:
                candidates.append(anchor)
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.market_volume)


def inspect_player_prop_candidates(
    match_name: str,
    spec: MarketSpec,
    events: list[dict],
    *,
    min_volume: float,
) -> list[PlayerPropCandidate]:
    """Return same-player Polymarket prop candidates and exclusion reasons.

    This is a diagnostics surface only. The production anchor still requires an
    `eligible` candidate: exact player, family, threshold, period, price, and
    volume.
    """

    if not spec.player or spec.family not in PLAYER_PROP_FAMILIES:
        return []
    if spec.period != MATCH:
        return []
    expected = _expected_player_prop_text(spec)
    if expected is None:
        return []
    first, second = match_teams(match_name)
    candidates: list[PlayerPropCandidate] = []
    for event in events:
        if not _event_matches(event, first, second):
            continue
        event_title = str(event.get("title") or "")
        for market in event.get("markets") or []:
            candidate = _player_prop_candidate_from_market(
                event_title,
                market,
                spec,
                min_volume=min_volume,
            )
            if candidate:
                candidates.append(candidate)
    return sorted(
        candidates,
        key=lambda item: (
            item.status != "eligible",
            -item.market_volume,
            item.market_question,
        ),
    )


def count_prop_anchor_for_market(
    match_name: str,
    spec: MarketSpec,
    events: list[dict],
    *,
    min_volume: float,
) -> DirectMarketAnchor | None:
    """Return an exact Polymarket count-market anchor for narrow corner props.

    Count markets are noisier than goal markets, so this is limited to exact
    total/team corner O/U markets and should be used with a stricter volume
    floor than ordinary goal anchors.
    """

    if spec.player or spec.family not in COUNT_PROP_FAMILIES:
        return None
    if spec.stat != "corners":
        return None
    first, second = match_teams(match_name)
    candidates: list[DirectMarketAnchor] = []
    for event in events:
        if not _event_matches(event, first, second):
            continue
        for market in event.get("markets") or []:
            anchor = _count_prop_anchor_from_market(str(event.get("title") or ""), market, spec)
            if anchor and anchor.market_volume >= min_volume:
                candidates.append(anchor)
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.market_volume)


def _loads_maybe_json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return ast.literal_eval(value)
    return value


def _volume(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _event_matches(event: dict, first: str, second: str) -> bool:
    title = str(event.get("title") or "")
    base = title.split(" - ", 1)[0]
    if " vs" not in base.lower():
        return False
    left, _, right = base.partition(" vs")
    pair = {left.strip().rstrip("."), right.lstrip(". ").strip()}
    return (
        any(names_match(first, team) for team in pair)
        and any(names_match(second, team) for team in pair)
    )


def _outcome_probability(market: dict, target: str, target_team: str | None = None) -> float | None:
    outcomes = _loads_maybe_json(market.get("outcomes"))
    prices = _loads_maybe_json(market.get("outcomePrices"))
    if not outcomes or not prices:
        return None
    pairs: list[tuple[str, float]] = []
    for outcome, price in zip(outcomes, prices):
        try:
            pairs.append((str(outcome), float(price)))
        except (TypeError, ValueError):
            continue
    total = sum(price for _outcome, price in pairs)
    if total <= 0:
        return None
    for outcome, price in pairs:
        if _outcome_matches(outcome, target, target_team):
            return price / total
    return None


def _outcome_matches(outcome: str, target: str, target_team: str | None) -> bool:
    if target_team is not None:
        return names_match(target_team, outcome)
    return outcome.strip().lower() == target.strip().lower()


def _anchor(
    event_title: str,
    market: dict,
    probability: float | None,
    outcome: str,
) -> DirectMarketAnchor | None:
    if probability is None:
        return None
    question = str(market.get("question") or "")
    return DirectMarketAnchor(
        probability=max(0.0, min(1.0, float(probability))),
        source="Polymarket direct",
        event_title=event_title,
        market_question=question,
        market_volume=_volume(market.get("volume")),
        outcome=outcome,
    )


def _anchor_from_market(
    event_title: str,
    market: dict,
    spec: MarketSpec,
    first: str,
    second: str,
) -> DirectMarketAnchor | None:
    question = str(market.get("question") or "")
    q = _norm(question)
    if spec.family == "total_goals":
        return _total_goals_anchor(event_title, market, spec, q)
    if spec.family == "team_goals_ge":
        return _team_total_anchor(event_title, market, spec, q)
    if spec.family == "team_clean_sheet":
        opponent = second if names_match(first, str(spec.team)) else first
        proxy = MarketSpec("team_goals_ge", team=opponent, stat="goals", threshold=1)
        anchor = _team_total_anchor(event_title, market, proxy, q, target="Under")
        if anchor:
            return anchor
    if spec.family == "team_scores_in_half":
        proxy = MarketSpec("team_goals_ge", team=spec.team, stat="goals", threshold=1, period=spec.period)
        return _team_total_anchor(event_title, market, proxy, q)
    if spec.family == "btts":
        if "both teams to score" in q and "first half" not in q and "second half" not in q:
            return _anchor(event_title, market, _outcome_probability(market, "Yes"), "Yes")
    if spec.family == "ht_team_winning":
        if "leading at halftime" in q and spec.team and _mentions_team(question, spec.team):
            return _anchor(event_title, market, _outcome_probability(market, "Yes"), "Yes")
    if spec.family == "ht_tied":
        if "draw at halftime" in q:
            return _anchor(event_title, market, _outcome_probability(market, "Yes"), "Yes")
    if spec.family == "team_first_goal_game":
        if _team_first_goal_subject_matches(question, spec.team):
            return _anchor(event_title, market, _outcome_probability(market, "Yes"), "Yes")
    if spec.family == "team_win_by_ge" and spec.team and spec.threshold:
        line = spec.threshold - 0.5
        if f"spread: " in q and _line_matches(q, line) and _mentions_team(question, spec.team):
            return _anchor(
                event_title,
                market,
                _outcome_probability(market, str(spec.team), target_team=str(spec.team)),
                str(spec.team),
            )
    return None


def _player_prop_anchor_from_market(
    event_title: str,
    market: dict,
    spec: MarketSpec,
) -> DirectMarketAnchor | None:
    candidate = _player_prop_candidate_from_market(
        event_title,
        market,
        spec,
        min_volume=0,
    )
    if not candidate or candidate.status not in {"eligible", "below_volume"}:
        return None
    if candidate.probability is None:
        return None
    return _anchor(event_title, market, candidate.probability, "Yes")


def _expected_player_prop_text(spec: MarketSpec) -> str | None:
    if spec.family == "player_scores":
        return "1+ goals"
    if spec.family == "player_scores_or_assists":
        return "1+ goals + assists"
    if spec.family == "player_saves_ge":
        threshold = spec.threshold or 1
        return f"{threshold}+ saves"
    if spec.family == "player_sot_ge":
        threshold = spec.threshold or 1
        return f"{threshold}+ shots on target"
    return None


def _player_prop_candidate_from_market(
    event_title: str,
    market: dict,
    spec: MarketSpec,
    *,
    min_volume: float,
) -> PlayerPropCandidate | None:
    question = str(market.get("question") or "")
    if ":" not in question:
        return None
    subject, _, prop = question.partition(":")
    if not spec.player or not _player_name_matches(str(spec.player), subject):
        return None
    prop_norm = _norm(prop)
    expected = _expected_player_prop_text(spec)
    if expected is None:
        return None
    probability = _outcome_probability(market, "Yes")
    volume = _volume(market.get("volume"))
    if prop_norm != expected:
        status = "prop_mismatch"
        reason = f"expected {expected}; saw {prop_norm}"
    elif probability is None:
        status = "missing_probability"
        reason = "matching prop but no usable Yes price"
    elif volume < min_volume:
        status = "below_volume"
        reason = f"matching prop but volume ${volume:,.0f} < ${min_volume:,.0f}"
    else:
        status = "eligible"
        reason = "exact player/family/threshold/volume match"
    return PlayerPropCandidate(
        status=status,
        reason=reason,
        probability=probability,
        event_title=event_title,
        market_question=question,
        market_volume=volume,
        expected_prop=expected,
        actual_prop=prop_norm,
    )


def _count_prop_anchor_from_market(
    event_title: str,
    market: dict,
    spec: MarketSpec,
) -> DirectMarketAnchor | None:
    question = str(market.get("question") or "")
    q = _norm(question)
    if "total corners" not in q and " corners" not in q:
        return None
    if "o/u" not in q or spec.threshold is None:
        return None
    if not _period_matches(q, spec.period):
        return None
    line = spec.threshold - 0.5
    if not _line_matches(q, line):
        return None
    if spec.family == "total_count_ge":
        if not _is_corner_match_total_question(q, spec.period):
            return None
    elif spec.family == "team_count_ge":
        if not spec.team or not _corner_team_total_subject_matches(question, spec.team):
            return None
    else:
        return None
    return _anchor(event_title, market, _outcome_probability(market, "Over"), "Over")


def _total_goals_anchor(event_title: str, market: dict, spec: MarketSpec, q: str) -> DirectMarketAnchor | None:
    if spec.threshold is None:
        return None
    if "total corners" in q or "total cards" in q or "shots" in q:
        return None
    if not _period_matches(q, spec.period):
        return None
    if "o/u" not in q:
        return None
    line = spec.threshold - 0.5 if spec.direction == ">=" else spec.threshold + 0.5
    if not _line_matches(q, line) or not _is_match_total_question(q, spec.period):
        return None
    target = "Over" if spec.direction == ">=" else "Under"
    return _anchor(event_title, market, _outcome_probability(market, target), target)


def _team_total_anchor(
    event_title: str,
    market: dict,
    spec: MarketSpec,
    q: str,
    *,
    target: str = "Over",
) -> DirectMarketAnchor | None:
    if spec.threshold is None or not spec.team:
        return None
    question = str(market.get("question") or "")
    if "corners" in q or "cards" in q or "shots" in q:
        return None
    if not _period_matches(q, spec.period):
        return None
    if "o/u" not in q or not _team_total_subject_matches(question, spec.team):
        return None
    line = spec.threshold - 0.5
    if not _line_matches(q, line):
        return None
    return _anchor(event_title, market, _outcome_probability(market, target), target)


def _period_matches(q: str, period: str) -> bool:
    if period == MATCH:
        return "1st half" not in q and "2nd half" not in q and "halftime" not in q
    if period == H1:
        return "1st half" in q
    if period == H2:
        return "2nd half" in q
    return False


def _is_match_total_question(q: str, period: str) -> bool:
    if period == MATCH:
        return re.search(r":\s*o/u\s+\d+(?:\.\d+)?", q) is not None
    if period == H1:
        return re.search(r":\s*1st half\s+o/u\s+\d+(?:\.\d+)?", q) is not None
    if period == H2:
        return re.search(r":\s*2nd half\s+o/u\s+\d+(?:\.\d+)?", q) is not None
    return False


def _is_corner_match_total_question(q: str, period: str) -> bool:
    if period == MATCH:
        return re.search(r":\s*o/u\s+\d+(?:\.\d+)?\s+total corners", q) is not None
    if period == H1:
        return re.search(r":\s*1st half\s+o/u\s+\d+(?:\.\d+)?\s+total corners", q) is not None
    if period == H2:
        return re.search(r":\s*2nd half\s+o/u\s+\d+(?:\.\d+)?\s+total corners", q) is not None
    return False


def _line_matches(q: str, line: float) -> bool:
    target = f"{line:.1f}"
    return re.search(rf"\b{re.escape(target)}\b", q) is not None


def _mentions_team(text: str, team: str) -> bool:
    return names_match(team, text)


def _team_total_subject_matches(question: str, team: str) -> bool:
    if ":" not in question:
        return False
    after_colon = question.split(":", 1)[1]
    before_ou = re.split(r"\bO/U\b", after_colon, maxsplit=1, flags=re.I)[0]
    subject = re.sub(r"\b(?:1st|2nd)\s+Half\b", "", before_ou, flags=re.I).strip()
    return bool(subject) and names_match(team, subject)


def _corner_team_total_subject_matches(question: str, team: str) -> bool:
    if ":" not in question:
        return False
    after_colon = question.split(":", 1)[1]
    before_ou = re.split(r"\bO/U\b", after_colon, maxsplit=1, flags=re.I)[0]
    subject = re.sub(r"\b(?:1st|2nd)\s+Half\b", "", before_ou, flags=re.I).strip()
    return bool(subject) and names_match(team, subject)


def _team_first_goal_subject_matches(question: str, team: str | None) -> bool:
    if not team:
        return False
    q = _norm(question)
    if "neither team" in q or "no goal" in q:
        return False
    if "to score first" not in q:
        return False
    if ":" not in question:
        return False
    after_colon = question.split(":", 1)[1]
    subject = re.split(r"\bto score first\b", after_colon, maxsplit=1, flags=re.I)[0].strip(" ?")
    return bool(subject) and names_match(team, subject)


def _player_name_matches(expected: str, subject: str) -> bool:
    expected_tokens = _name_tokens(expected)
    subject_tokens = _name_tokens(subject)
    if not expected_tokens or not subject_tokens:
        return False
    if expected_tokens == subject_tokens:
        return True
    # Allow middle-name expansions such as "Julio Enciso" ->
    # "Julio Cesar Enciso", but avoid one-token surname-only matches.
    return len(expected_tokens) >= 2 and expected_tokens <= subject_tokens


def _name_tokens(value: str) -> set[str]:
    import unicodedata

    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return set(re.findall(r"[a-z0-9]+", folded.lower()))


def _norm(value: str) -> str:
    return re.sub(r"\s+", " ", value.lower().replace(" vs. ", " vs ")).strip()
