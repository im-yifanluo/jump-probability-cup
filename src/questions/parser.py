"""Parse Probability Cup market questions into structured MarketSpecs.

Every question is one of ~25 templates. A MarketSpec captures the family plus
its parameters (subject team/player, stat, threshold, period, opponent), so
pricing code never touches question text. `parse()` raises on anything it
doesn't recognize — unpriced markets must be loud, not silent.

One template is genuinely ambiguous: "Will X have at least N shot(s) on
target?" is asked about both teams and players, so `parse()` takes the set of
team names appearing in the event and classifies X by membership.

Stats: goals / corners / cards / sot / fouls / offsides.
Periods: "match" (full game), "1h" (first half — includes "at halftime"
questions, which read the state/counts after 45 minutes), "2h" (second half).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from questions.teams import EXTERNAL_ALIASES

MATCH, H1, H2 = "match", "1h", "2h"


@dataclass(frozen=True)
class MarketSpec:
    family: str
    team: str | None = None
    opponent: str | None = None
    player: str | None = None
    stat: str | None = None
    threshold: int | None = None
    direction: str = ">="
    period: str = MATCH
    params: dict = field(default_factory=dict, compare=False)


class UnparsedQuestion(ValueError):
    pass


# Each rule: (compiled regex, builder(match, teams) -> MarketSpec).
# Order matters — more specific templates must come before generic ones.
_RULES: list[tuple[re.Pattern, callable]] = []


def _norm_team_subject(name: str) -> str:
    import unicodedata

    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    normalized = re.sub(r"[^a-z0-9 ]+", " ", folded.lower()).strip()
    return re.sub(r"^the\s+", "", normalized)


def _team_subject(name: str, teams) -> str | None:
    normalized = _norm_team_subject(name)
    for team in teams:
        aliases = {team, *EXTERNAL_ALIASES.get(team, set())}
        if normalized in {_norm_team_subject(alias) for alias in aliases}:
            return team
    return None


_PLAYER_TEAM_SUFFIX = re.compile(r"\s*\((?P<team>[^)]*)\)\s*$")


def _player_name_and_team(name: str, teams) -> tuple[str, str | None]:
    """Return the clean player name plus an explicit team hint when the
    question writes player props as "Name (Team)".

    The hint is deliberately conservative: only a parenthetical suffix is used,
    and it must resolve through the same team alias table as normal team
    subjects. This prevents the player model from falling back to match-average
    strength for prior-only players whose team is supplied in the market text.
    """
    m = _PLAYER_TEAM_SUFFIX.search(name)
    clean = _PLAYER_TEAM_SUFFIX.sub("", name).strip()
    return clean, _team_subject(m["team"], teams) if m else None


def _player_name(name: str) -> str:
    return _player_name_and_team(name, frozenset())[0]


def _player_params(team: str | None) -> dict:
    return {"player_team": team} if team else {}


def _rule(pattern: str):
    def register(builder):
        _RULES.append((re.compile(pattern), builder))
        return builder
    return register


# --- match outcome / state ---------------------------------------------------

@_rule(r"^Will (?P<team>.+?) advance to the Round of 16\?$")
def _(m, teams):
    return MarketSpec("team_advance", team=m["team"])

@_rule(r"^Will (?P<team>.+?) win by (?P<n>\d+) or more goals\?$")
def _(m, teams):
    return MarketSpec("team_win_by_ge", team=m["team"], stat="goals", threshold=int(m["n"]))


@_rule(r"^Will (?P<team>.+?) win(?: the match)?\?$")
def _(m, teams):
    return MarketSpec("team_win", team=m["team"])


@_rule(r"^(?:At halftime, will the match be tied|Will the match be tied at halftime)\?$")
def _(m, teams):
    return MarketSpec("ht_tied", period=H1)


@_rule(r"^At halftime, will (?P<team>.+?) be winning\?$")
def _(m, teams):
    return MarketSpec("ht_team_winning", team=m["team"], period=H1)


@_rule(r"^Will (?P<team>.+?) be ahead at halftime\?$")
def _(m, teams):
    return MarketSpec("ht_team_winning", team=m["team"], period=H1)


# --- goals ---------------------------------------------------------------------

@_rule(r"^Will both teams score AND the match have (?P<n>\d+) or more total goals\?$")
def _(m, teams):
    return MarketSpec("btts_and_total_ge", stat="goals", threshold=int(m["n"]))


@_rule(r"^Will both teams score\?$")
def _(m, teams):
    return MarketSpec("btts", stat="goals")


@_rule(r"^Will the match end in a tie\?$")
def _(m, teams):
    return MarketSpec("match_drawn", stat="goals")


@_rule(r"^Will (?P<team>.+?) score in both halves\?$")
def _(m, teams):
    return MarketSpec("team_scores_both_halves", team=m["team"], stat="goals")


@_rule(r"^Will (?P<team>.+?) keep a clean sheet\?$")
def _(m, teams):
    return MarketSpec("team_clean_sheet", team=m["team"], stat="goals")


@_rule(r"^Will the match have (?P<n>\d+) or (?P<dir>more|fewer) total goals\?$")
def _(m, teams):
    return MarketSpec("total_goals", stat="goals", threshold=int(m["n"]),
                      direction=">=" if m["dir"] == "more" else "<=")


@_rule(r"^Will the second half have (?P<n>\d+) or more total goals\?$")
def _(m, teams):
    return MarketSpec("total_goals", stat="goals", threshold=int(m["n"]), period=H2)


@_rule(r"^Will the first half (?:have|produce) (?P<n>\d+) or more (?:total )?goals\?$")
def _(m, teams):
    return MarketSpec("total_goals", stat="goals", threshold=int(m["n"]), period=H1)


@_rule(r"^Will the second half (?:have|produce) more (?:total )?goals than the first half\?$")
def _(m, teams):
    return MarketSpec("h2_more_goals_than_h1", stat="goals")


@_rule(r"^Will any player score (?:(?P<n1>\d+) or more|at least (?P<n2>\d+)) goals?(?: \(excluding own goals\))?\?$")
def _(m, teams):
    return MarketSpec("any_player_goals_ge", stat="goals", threshold=int(m["n1"] or m["n2"]))


@_rule(r"^Will (?P<team>.+?) score (?:(?P<n1>\d+) or more(?: total)? goals|at least (?P<n2>\d+) goals?)\?$")
def _(m, teams):
    return MarketSpec("team_goals_ge", team=m["team"], stat="goals",
                      threshold=int(m["n1"] or m["n2"]))


@_rule(r"^Will (?P<team>.+?) score the first goal of the game and (?P<opp>.+?) score in the second half\?$")
def _(m, teams):
    # Registered before the plain "score in the X half" rule, which would
    # otherwise swallow this combo template.
    return MarketSpec("first_goal_and_other_scores_2h", team=m["team"],
                      opponent=m["opp"], stat="goals")


@_rule(r"^Will (?P<team>.+?) score the first goal of the (?:game|match)\?$")
def _(m, teams):
    return MarketSpec("team_first_goal_game", team=m["team"], stat="goals")


@_rule(r"^Will (?P<team>.+?) score in the (?P<half>first|second) half\?$")
def _(m, teams):
    return MarketSpec("team_scores_in_half", team=m["team"], stat="goals",
                      period=H1 if m["half"] == "first" else H2)


@_rule(r"^Will (?P<team>.+?) score more goals than (?P<opp>.+?) in the second half\?$")
def _(m, teams):
    return MarketSpec("team_more_goals_2h", team=m["team"], opponent=m["opp"],
                      stat="goals", period=H2)


@_rule(r"^Will (?P<team>.+?) score the first goal of the second half\?$")
def _(m, teams):
    return MarketSpec("team_first_goal_2h", team=m["team"], stat="goals", period=H2)


# --- penalties / red cards -------------------------------------------------------

@_rule(r"^Will a penalty kick be awarded(?: in the match)?\?$")
def _(m, teams):
    return MarketSpec("penalty_awarded")


@_rule(r"^Will a penalty kick be awarded OR a red card be shown(?: in the match)?\?$")
def _(m, teams):
    return MarketSpec("penalty_or_red")


@_rule(r"^Will a red card be shown(?: in the match)?\?$")
def _(m, teams):
    return MarketSpec("red_card")


# --- timed / special event markets -------------------------------------------

@_rule(r"^Will a goal be scored before the first hydration break\?$")
def _(m, teams):
    return MarketSpec("goal_before_hydration", stat="goals", params={"minute_cut": 30})


@_rule(r"^Will a goal be scored after the second hydration break\?$")
def _(m, teams):
    return MarketSpec("goal_after_second_hydration", stat="goals", params={"minute_cut": 75})


@_rule(r"^Will a goal be scored in (?P<half>first|second)-half stoppage(?: \(added\))? time\?$")
def _(m, teams):
    return MarketSpec(
        "goal_in_stoppage",
        stat="goals",
        period=H1 if m["half"] == "first" else H2,
    )


@_rule(r"^Will either team be ruled offside before the first hydration break\?$")
def _(m, teams):
    return MarketSpec("offside_before_hydration", stat="offsides", params={"minute_cut": 30})


@_rule(r"^Will a card be shown after the second hydration break, including any extra time\?$")
def _(m, teams):
    return MarketSpec(
        "card_after_second_hydration",
        stat="cards",
        params={"minute_cut": 75, "include_extra_time": True},
    )


@_rule(r"^Will a substitute score a goal(?: \(excluding own goals\))?\?$")
def _(m, teams):
    return MarketSpec("substitute_scores", stat="goals")


@_rule(r"^Will an own goal be scored\?$")
def _(m, teams):
    return MarketSpec("own_goal", stat="goals")


@_rule(r"^Will a goal be scored from outside the penalty area\?$")
def _(m, teams):
    return MarketSpec("outside_box_goal", stat="goals")


@_rule(r"^Will a header goal be scored\?$")
def _(m, teams):
    return MarketSpec("header_goal", stat="goals")


@_rule(r"^Will a substitution be made before halftime\?$")
def _(m, teams):
    return MarketSpec("substitution_before_halftime")


@_rule(r"^Will any player score more than (?P<n>\d+) goal(?:s)?(?: \(excluding own goals\))?\?$")
def _(m, teams):
    return MarketSpec("any_player_goals_ge", stat="goals", threshold=int(m["n"]) + 1)


@_rule(r"^Will any player record (?P<n>\d+) or more shots? on target\?$")
def _(m, teams):
    return MarketSpec("any_player_sot_ge", stat="sot", threshold=int(m["n"]))


# --- total counts (both teams combined) -------------------------------------------

@_rule(r"^Will there be (?P<n>\d+) or more total shots \(on and off target\)\?$")
def _(m, teams):
    return MarketSpec("total_count_ge", stat="shots", threshold=int(m["n"]))


@_rule(r"^Will (?P<n>\d+) or more corner kicks be taken before the first hydration break\?$")
def _(m, teams):
    return MarketSpec(
        "total_count_before_hydration",
        stat="corners",
        threshold=int(m["n"]),
        params={"minute_cut": 30},
    )

@_rule(r"^Will there be (?P<n>\d+) or more total cards shown(?P<h2> in the second half)?\?$")
def _(m, teams):
    return MarketSpec("total_count_ge", stat="cards", threshold=int(m["n"]),
                      period=H2 if m["h2"] else MATCH)


@_rule(r"^Will a card be shown in the (?P<half>first|second) half\?$")
def _(m, teams):
    return MarketSpec("total_count_ge", stat="cards", threshold=1,
                      period=H1 if m["half"] == "first" else H2)


@_rule(r"^Will there be (?P<n>\d+) or more total corner kicks(?: in the (?P<period>second half|match))?\?$")
def _(m, teams):
    return MarketSpec("total_count_ge", stat="corners", threshold=int(m["n"]),
                      period=H2 if m["period"] == "second half" else MATCH)


@_rule(r"^Will there be (?P<n>\d+) or more total shots on target(?P<h2> in the second half)?\?$")
def _(m, teams):
    return MarketSpec("total_count_ge", stat="sot", threshold=int(m["n"]),
                      period=H2 if m["h2"] else MATCH)


@_rule(r"^Will there be (?P<n>\d+) or more (?:total )?offside(?: calls|s)?\?$")
def _(m, teams):
    return MarketSpec("total_count_ge", stat="offsides", threshold=int(m["n"]))


# --- "both teams" thresholds (must precede the per-team/player SOT rule) ----------

@_rule(r"^(?:At halftime, will|Will) both teams have at least (?P<n>\d+) shots? on target(?P<h2> in the second half)?\?$")
def _(m, teams):
    return MarketSpec("both_teams_count_ge", stat="sot", threshold=int(m["n"]),
                      period=H2 if m["h2"] else H1)


@_rule(r"^Will both teams receive at least (?:one|(?P<n>\d+)) cards?\?$")
def _(m, teams):
    return MarketSpec("both_teams_count_ge", stat="cards",
                      threshold=int(m["n"]) if m["n"] else 1, period=MATCH)


# --- per-team counts ----------------------------------------------------------------

@_rule(r"^Will (?P<team>.+?) be caught offside (?P<n>\d+) or more times\?$")
def _(m, teams):
    return MarketSpec("team_count_ge", team=m["team"], stat="offsides", threshold=int(m["n"]))


@_rule(r"^Will (?P<team>.+?) receive at least (?P<n>\d+) cards? in the second half\?$")
def _(m, teams):
    return MarketSpec("team_count_ge", team=m["team"], stat="cards",
                      threshold=int(m["n"]), period=H2)


@_rule(r"^Will (?P<team>.+?) have (?:(?P<n1>\d+) or more|at least (?P<n2>\d+)) corner kicks?(?P<h1> in the first half)?\?$")
def _(m, teams):
    return MarketSpec("team_count_ge", team=m["team"], stat="corners",
                      threshold=int(m["n1"] or m["n2"]), period=H1 if m["h1"] else MATCH)


@_rule(r"^Will (?P<name>.+?) have (?:(?P<n1>\d+) or more|at least (?P<n2>\d+)) shots? on target(?P<h2> in the second half)?\?$")
def _(m, teams):
    # Ambiguous template: subject is a team or a player — decide by membership.
    period = H2 if m["h2"] else MATCH
    threshold = int(m["n1"] or m["n2"])
    team = _team_subject(m["name"], teams)
    if team:
        return MarketSpec("team_count_ge", team=team, stat="sot",
                          threshold=threshold, period=period)
    player, player_team = _player_name_and_team(m["name"], teams)
    return MarketSpec("player_sot_ge", player=player, stat="sot",
                      threshold=threshold, period=period,
                      params=_player_params(player_team))


# --- head-to-head count comparisons (ties resolve to No) ----------------------------

@_rule(r"^At halftime, will (?P<team>.+?) have more (?P<stat>corner kicks|shots on target) than (?P<opp>.+?)\?$")
def _(m, teams):
    return MarketSpec("team_more_count_than", team=m["team"], opponent=m["opp"],
                      stat="corners" if "corner" in m["stat"] else "sot", period=H1)


@_rule(r"^In the second half, will (?P<team>.+?) have more (?P<stat>corner kicks|shots on target) than (?P<opp>.+?)\?$")
def _(m, teams):
    return MarketSpec("team_more_count_than", team=m["team"], opponent=m["opp"],
                      stat="corners" if "corner" in m["stat"] else "sot", period=H2)


@_rule(r"^Will (?P<team>.+?) have more (?P<stat>corner kicks|shots on target) than (?P<opp>.+?)(?P<h2> in the second half)?\?$")
def _(m, teams):
    return MarketSpec("team_more_count_than", team=m["team"], opponent=m["opp"],
                      stat="corners" if "corner" in m["stat"] else "sot",
                      period=H2 if m["h2"] else MATCH)


@_rule(r"^Will (?P<team>.+?) finish with more corner kicks than (?P<opp>.+?)\?$")
def _(m, teams):
    return MarketSpec("team_more_count_than", team=m["team"], opponent=m["opp"],
                      stat="corners", period=MATCH)


@_rule(r"^Will (?P<team>.+?) commit more fouls than (?P<opp>.+?)\?$")
def _(m, teams):
    return MarketSpec("team_more_count_than", team=m["team"], opponent=m["opp"],
                      stat="fouls", period=MATCH)


@_rule(r"^Will (?P<team>.+?) receive more cards than (?P<opp>.+?)\?$")
def _(m, teams):
    return MarketSpec("team_more_count_than", team=m["team"], opponent=m["opp"],
                      stat="cards", period=MATCH)


# --- player props ----------------------------------------------------------------------

@_rule(r"^Will (?P<player>.+?) score a goal \(excluding own goals\)\?$")
def _(m, teams):
    team = _team_subject(m["player"], teams)
    if team:
        return MarketSpec("team_goals_ge", team=team, stat="goals", threshold=1)
    player, player_team = _player_name_and_team(m["player"], teams)
    return MarketSpec("player_scores", player=player,
                      params=_player_params(player_team))


@_rule(r"^Will (?P<player>.+?) score or assist a goal \(excluding own goals\)\?$")
def _(m, teams):
    player, player_team = _player_name_and_team(m["player"], teams)
    return MarketSpec("player_scores_or_assists", player=player,
                      params=_player_params(player_team))


# Knockout-round markets re-phrase the group-stage templates with a regulation /
# extra-time qualifier ("... in regulation (90 minutes + stoppage time)", "...,
# excluding extra time", "Will regulation (90 minutes + stoppage time) end ..."),
# because KO ties can go to extra time. Our models price 90-minute regulation, so
# the qualifier is a semantic no-op — strip it to the base group-stage question.
_REG_SUFFIX = re.compile(
    r"\s+(?:in|during|of)\s+regulation\s*\(90 minutes\s*(?:\+|plus)\s*stoppage time\)", re.I)
_REG_SUBJECT = re.compile(
    r"\bregulation\s*\(90 minutes\s*(?:\+|plus)\s*stoppage time\)", re.I)
_EXCL_ET = re.compile(r",?\s+excluding extra time", re.I)


def _normalize(q: str) -> str:
    q = _REG_SUFFIX.sub("", q)              # "...total goals in regulation (...)?" -> "...total goals?"
    q = _EXCL_ET.sub("", q)
    q = _REG_SUBJECT.sub("the match", q)    # "Will regulation (...) end in a tie?" -> "...the match..."
    return re.sub(r"\s{2,}", " ", q).replace(" ?", "?").strip()


def parse(question: str, teams: frozenset[str] | set[str] = frozenset()) -> MarketSpec:
    """Question text -> MarketSpec. `teams` is the set of full team names in
    the event (used to disambiguate team-vs-player templates).
    Raises UnparsedQuestion if no template matches."""
    q = _normalize(question.strip())
    for pattern, builder in _RULES:
        m = pattern.match(q)
        if m:
            spec = builder(m, teams)
            team = _team_subject(spec.team, teams) if spec.team else None
            opponent = _team_subject(spec.opponent, teams) if spec.opponent else None
            if team or opponent:
                return replace(spec, team=team or spec.team, opponent=opponent or spec.opponent)
            return spec
    raise UnparsedQuestion(question)
