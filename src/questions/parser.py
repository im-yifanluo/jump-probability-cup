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
from dataclasses import dataclass, field

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


def _rule(pattern: str):
    def register(builder):
        _RULES.append((re.compile(pattern), builder))
        return builder
    return register


# --- match outcome / state ---------------------------------------------------

@_rule(r"^Will (?P<team>.+?) win the match\?$")
def _(m, teams):
    return MarketSpec("team_win", team=m["team"])


@_rule(r"^(?:At halftime, will the match be tied|Will the match be tied at halftime)\?$")
def _(m, teams):
    return MarketSpec("ht_tied", period=H1)


@_rule(r"^At halftime, will (?P<team>.+?) be winning\?$")
def _(m, teams):
    return MarketSpec("ht_team_winning", team=m["team"], period=H1)


# --- goals ---------------------------------------------------------------------

@_rule(r"^Will both teams score AND the match have (?P<n>\d+) or more total goals\?$")
def _(m, teams):
    return MarketSpec("btts_and_total_ge", stat="goals", threshold=int(m["n"]))


@_rule(r"^Will the match have (?P<n>\d+) or (?P<dir>more|fewer) total goals\?$")
def _(m, teams):
    return MarketSpec("total_goals", stat="goals", threshold=int(m["n"]),
                      direction=">=" if m["dir"] == "more" else "<=")


@_rule(r"^Will the second half have (?P<n>\d+) or more total goals\?$")
def _(m, teams):
    return MarketSpec("total_goals", stat="goals", threshold=int(m["n"]), period=H2)


@_rule(r"^Will the second half have more (?:total )?goals than the first half\?$")
def _(m, teams):
    return MarketSpec("h2_more_goals_than_h1", stat="goals")


@_rule(r"^Will (?P<team>.+?) score (?:(?P<n1>\d+) or more total goals|at least (?P<n2>\d+) goals?)\?$")
def _(m, teams):
    return MarketSpec("team_goals_ge", team=m["team"], stat="goals",
                      threshold=int(m["n1"] or m["n2"]))


@_rule(r"^Will (?P<team>.+?) score the first goal of the game and (?P<opp>.+?) score in the second half\?$")
def _(m, teams):
    # Registered before the plain "score in the X half" rule, which would
    # otherwise swallow this combo template.
    return MarketSpec("first_goal_and_other_scores_2h", team=m["team"],
                      opponent=m["opp"], stat="goals")


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

@_rule(r"^Will a penalty kick be awarded in the match\?$")
def _(m, teams):
    return MarketSpec("penalty_awarded")


@_rule(r"^Will a penalty kick be awarded OR a red card be shown(?: in the match)?\?$")
def _(m, teams):
    return MarketSpec("penalty_or_red")


# --- total counts (both teams combined) -------------------------------------------

@_rule(r"^Will there be (?P<n>\d+) or more total cards shown(?P<h2> in the second half)?\?$")
def _(m, teams):
    return MarketSpec("total_count_ge", stat="cards", threshold=int(m["n"]),
                      period=H2 if m["h2"] else MATCH)


@_rule(r"^Will there be (?P<n>\d+) or more total corner kicks(?: in the (?P<period>second half|match))?\?$")
def _(m, teams):
    return MarketSpec("total_count_ge", stat="corners", threshold=int(m["n"]),
                      period=H2 if m["period"] == "second half" else MATCH)


@_rule(r"^Will there be (?P<n>\d+) or more total shots on target(?P<h2> in the second half)?\?$")
def _(m, teams):
    return MarketSpec("total_count_ge", stat="sot", threshold=int(m["n"]),
                      period=H2 if m["h2"] else MATCH)


# --- "both teams" thresholds (must precede the per-team/player SOT rule) ----------

@_rule(r"^(?:At halftime, will|Will) both teams have at least (?P<n>\d+) shots? on target(?P<h2> in the second half)?\?$")
def _(m, teams):
    return MarketSpec("both_teams_count_ge", stat="sot", threshold=int(m["n"]),
                      period=H2 if m["h2"] else H1)


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
    if m["name"] in teams:
        return MarketSpec("team_count_ge", team=m["name"], stat="sot",
                          threshold=threshold, period=period)
    return MarketSpec("player_sot_ge", player=m["name"], stat="sot",
                      threshold=threshold, period=period)


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
    return MarketSpec("player_scores", player=m["player"])


@_rule(r"^Will (?P<player>.+?) score or assist a goal \(excluding own goals\)\?$")
def _(m, teams):
    return MarketSpec("player_scores_or_assists", player=m["player"])


def parse(question: str, teams: frozenset[str] | set[str] = frozenset()) -> MarketSpec:
    """Question text -> MarketSpec. `teams` is the set of full team names in
    the event (used to disambiguate team-vs-player templates).
    Raises UnparsedQuestion if no template matches."""
    q = question.strip()
    for pattern, builder in _RULES:
        m = pattern.match(q)
        if m:
            return builder(m, teams)
    raise UnparsedQuestion(question)
