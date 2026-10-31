"""The researched per-match inputs the agent maintains, read for one fixture.

These files carry facts the market odds don't (never raw probabilities):

    data/inputs/referee_overrides.yaml  {match: {referee, cards_per_game, source}}
    data/inputs/player_overrides.yaml   stable player rates + scorer_prop_prob
    data/inputs/match_lineups.yaml      confirmed minutes, written by the lineup pull

Every reader is scoped to one match name, so a fact researched for one game
can never leak into the next.
"""
from __future__ import annotations

import yaml

from probcup import config
from probcup.models import calibration as cal

PLAYER_OVERRIDES = config.PLAYER_OVERRIDES
REFEREE_OVERRIDES = config.REFEREE_OVERRIDES
MATCH_LINEUPS = config.MATCH_LINEUPS


def referee_card_factor(match_name: str) -> float:
    """Referee strictness multiplier for a match's card markets, from
    data/inputs/referee_overrides.yaml ({match: {referee, cards_per_game}}). 1.0
    when the referee is unknown — the agent researches and fills it pre-match.
    Capped at 1.0: only a lenient referee moves the card markets (see
    calibration.REFEREE_FACTOR_CAP for why)."""
    if not REFEREE_OVERRIDES.exists():
        return 1.0
    refs = yaml.safe_load(REFEREE_OVERRIDES.read_text()) or {}
    entry = refs.get(match_name)
    if not entry or "cards_per_game" not in entry:
        return 1.0
    return min(cal.REFEREE_FACTOR_CAP,
               max(cal.REFEREE_FACTOR_FLOOR,
                   float(entry["cards_per_game"]) / cal.TOURNAMENT_CARDS_PER_MATCH))


def scorer_props(match_name: str | None = None) -> dict[str, float]:
    """De-margined anytime-scorer props from the overrides (key scorer_prop_prob).
    Markets beat models, so when one exists MatchPricer uses it outright.

    A prop is OPPONENT-SPECIFIC (a de-margined price for one fixture), so it must
    be SCOPED to the match it was researched for via `scorer_prop_match`, or it
    rides into the team's next game and goes stale (the 06-29 Gyökeres/Balogun
    class). With a match_name given:
      tagged & matches this match -> used
      tagged & a DIFFERENT match  -> EXPIRED, skipped (loud), reverts to the model
      untagged                    -> used but LOUDLY flagged (please scope it)
    match_name=None keeps the legacy all-props behaviour for callers without it."""
    if not PLAYER_OVERRIDES.exists():
        return {}
    overrides = yaml.safe_load(PLAYER_OVERRIDES.read_text()) or {}
    props = {}
    for name, o in overrides.items():
        if not (isinstance(o, dict) and "scorer_prop_prob" in o):
            continue
        tag = o.get("scorer_prop_match")
        if match_name is None or tag == match_name:
            props[name] = o["scorer_prop_prob"]
        elif tag is None:
            props[name] = o["scorer_prop_prob"]
            print(f"  ** scorer_prop for {name} is UNSCOPED (no scorer_prop_match) — it "
                  f"applies to ANY match; add scorer_prop_match: {match_name!r} to scope it **")
        else:
            print(f"  ** scorer_prop for {name} EXPIRED (researched for {tag!r}, not "
                  f"{match_name!r}) — ignoring it, using the model **")
    return props


def match_minutes(match_name: str) -> dict[str, float]:
    """Confirmed expected_minutes for THIS match from data/inputs/match_lineups.yaml
    (written by the ESPN lineup pull from the posted XI). Empty when no lineup has
    been pulled — every player then defaults to 90 (everyone starts), the safe prior."""
    if not MATCH_LINEUPS.exists():
        return {}
    data = yaml.safe_load(MATCH_LINEUPS.read_text()) or {}
    return (data.get(match_name) or {}).get("minutes") or {}
