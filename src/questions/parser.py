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

    def __post_init__(self):
        # Canonicalize the team field: questions say "the Netherlands"/"the United
        # States" while match/team names drop the article.
        for fld in ("team", "opponent"):
            v = getattr(self, fld)
            if isinstance(v, str) and v.lower().startswith("the "):
                object.__setattr__(self, fld, v[4:])
        # Strip a trailing "(Team)" disambiguator from the player name. Some
        # fixtures phrase player props as "Will Kai Havertz (Germany) score…";
        # without this the player key is "Kai Havertz (Germany)", which matches no
        # StatsBomb history and no override, silently dropping the player to the
        # generic 0.12 prior. (2026-06-28: this was leaking every GER/PAR player prop.)
        if isinstance(self.player, str):
            stripped = re.sub(r"\s*\([^)]*\)\s*$", "", self.player).strip()
            if stripped and stripped != self.player:
                object.__setattr__(self, "player", stripped)


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

@_rule(r"^Will (?P<team>.+?) win by (?P<n>\d+) or more goals\?$")
def _(m, teams):
    return MarketSpec("team_win_by_ge", team=m["team"], stat="goals", threshold=int(m["n"]))


@_rule(r"^Will (?P<team>.+?) win(?: the match)?\?$")
def _(m, teams):
    return MarketSpec("team_win", team=m["team"])


@_rule(r"^Will (?P<team>.+?) keep a clean sheet\?$")
def _(m, teams):
    return MarketSpec("clean_sheet", team=m["team"])


@_rule(r"^Will (?P<team>.+?) advance to the (?:Round of 16|quarterfinals?|semifinals?|final)\?$")
def _(m, teams):
    # Round-agnostic: every KO round re-phrases the same tie-winner market.
    return MarketSpec("advance", team=m["team"])


@_rule(r"^Will the match be decided by a penalty shootout\?$")
def _(m, teams):
    return MarketSpec("shootout_decided", stat="goals")


@_rule(r"^Will exactly (?P<n>\d+) goals? be scored\?$")
def _(m, teams):
    return MarketSpec("total_goals_exact", stat="goals", threshold=int(m["n"]))


@_rule(r"^Will the match finish with exactly (?P<n>\d+) total goals?\?$")
def _(m, teams):
    return MarketSpec("total_goals_exact", stat="goals", threshold=int(m["n"]))


@_rule(r"^Will (?P<team>.+?) hold a lead at any point(?: in the match)?(?: \(excluding a penalty shootout\))?\?$")
def _(m, teams):
    return MarketSpec("hold_lead", team=m["team"], stat="goals")


@_rule(r"^Will at least one goal be scored in each half\?$")
def _(m, teams):
    return MarketSpec("goal_each_half", stat="goals")


@_rule(r"^Will there be more total cards than total goals\?$")
def _(m, teams):
    return MarketSpec("cards_gt_goals", stat="cards")


# --- QF templates (first seen POR/ESP 2026-07-06) ---

@_rule(r"^Will the match go to extra time\?$")
def _(m, teams):
    return MarketSpec("extra_time", stat="goals")


@_rule(r"^Will both halves have the same number of goals\?$")
def _(m, teams):
    return MarketSpec("same_goals_halves", stat="goals")


@_rule(r"^Will the first card of the match be shown before the first goal is scored\?$")
def _(m, teams):
    return MarketSpec("first_card_before_goal", stat="cards")


# --- SF/KO templates (first seen ARG/EGY + SUI/COL 2026-07-07) ---

@_rule(r"^Will the total number of goals be an odd number\?$")
def _(m, teams):
    return MarketSpec("total_goals_odd", stat="goals")


@_rule(r"^Will the match be decided by exactly one goal\?$")
def _(m, teams):
    return MarketSpec("decided_by_one_goal", stat="goals")


@_rule(r"^Will the first goal of the match be scored in the second half\?$")
def _(m, teams):
    return MarketSpec("first_goal_in_2h", stat="goals")


@_rule(r"^Will either team win both halves\?$")
def _(m, teams):
    return MarketSpec("either_wins_both_halves", stat="goals")


@_rule(r"^Will at least one card be shown in each half\?$")
def _(m, teams):
    return MarketSpec("card_each_half", stat="cards")


@_rule(r"^Will the first goal of the match be scored by a player other than (?P<names>.+?)\?$")
def _(m, teams):
    parts = re.split(r",\s*|\s+and\s+", m["names"])
    names = [p.strip() for p in parts if p.strip()]
    return MarketSpec("first_goal_not_players", stat="goals", params={"exclude": names})


@_rule(r"^Will (?P<player>.+?) make (?P<n>\d+) or more saves\?$")
def _(m, teams):
    return MarketSpec("gk_saves_ge", player=m["player"], stat="sot", threshold=int(m["n"]))


@_rule(r"^Will there be (?P<n>\d+) or more total substitutions(?: \(both teams combined\))?\?$")
def _(m, teams):
    return MarketSpec("total_subs_ge", stat="subs", threshold=int(m["n"]))


@_rule(r"^Will (?P<player>.+?) play the entire match\?$")
def _(m, teams):
    return MarketSpec("plays_full_match", player=m["player"])


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


@_rule(r"^Will (?P<team>.+?) score (?:(?P<n1>\d+) or more(?: total)? goals|at least (?P<n2>\d+) goals?)\?$")
def _(m, teams):
    # "any player score 2+ goals" is the squad-brace market, not a team-goals
    # market — otherwise "any player" is captured as a team (see the any_player_braces
    # rule below, which only catches the "more than 1 / 2 or more goals" phrasing).
    if m["team"].strip().lower() == "any player":
        return MarketSpec("any_player_braces", stat="goals")
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


# --- total counts (both teams combined) -------------------------------------------

@_rule(r"^Will there be (?P<n>\d+) or more total cards(?: shown)?(?P<h2> in the second half)?\?$")
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
    # Strip a leading article first: questions say "the United States"/"the
    # Netherlands" while the team set drops it, so without this a team-SOT count
    # market is misread as a player prop and floored to the 0.12 prior.
    period = H2 if m["h2"] else MATCH
    threshold = int(m["n1"] or m["n2"])
    name = m["name"]
    clean = name[4:] if name.lower().startswith("the ") else name
    if clean in teams:
        return MarketSpec("team_count_ge", team=clean, stat="sot",
                          threshold=threshold, period=period)
    # "any <TEAM> player" is the team-scoped squad market (POR/ESP QF template) —
    # this rule fires before the dedicated one below, so route it here too.
    squad = re.match(r"^any (.+?) player$", clean, re.I)
    if squad and squad.group(1) in teams:
        return MarketSpec("team_any_player_sot_ge", team=squad.group(1), stat="sot",
                          threshold=threshold, period=period)
    return MarketSpec("player_sot_ge", player=name, stat="sot",
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
    # "Will <TEAM> score a goal" is a TEAM-scores market (>=1 team goal), not a
    # player prop — otherwise the team name is captured as a player and falls to
    # the 0.12 prior (e.g. "Will DR Congo score a goal").
    subj = m["player"]
    clean = subj[4:] if subj.lower().startswith("the ") else subj
    if clean in teams:
        return MarketSpec("team_goals_ge", team=clean, stat="goals", threshold=1)
    # "Will a substitute score…" is the SQUAD substitute market, not a player named
    # "a substitute" (which silently priced on the 0.12 prior — cost -47 RBP on
    # BEL/SEN 2026-07-01 when a sub scored; the validated substitute model had it ~0.25).
    if clean.lower() in ("a substitute", "any substitute"):
        return MarketSpec("substitute_scores", stat="goals")
    return MarketSpec("player_scores", player=subj)


@_rule(r"^Will (?P<player>.+?) score or assist a goal \(excluding own goals\)\?$")
def _(m, teams):
    subj = m["player"]
    clean = subj[4:] if subj.lower().startswith("the ") else subj
    if clean in teams:  # a team can't "assist"; treat as team scores >=1
        return MarketSpec("team_goals_ge", team=clean, stat="goals", threshold=1)
    return MarketSpec("player_scores_or_assists", player=subj)


# --- timing markets (within-match windows: hydration breaks ~30'/~75', stoppage) ---

@_rule(r"^Will a goal be scored before the first hydration break\?$")
def _(m, teams):
    return MarketSpec("timing_event", stat="goals", threshold=1, params={"window": "before30"})


@_rule(r"^Will a goal be scored after the second hydration break(?: but before the end of regulation)?\?$")
def _(m, teams):
    return MarketSpec("timing_event", stat="goals", threshold=1, params={"window": "after75"})


@_rule(r"^Will a goal be scored in the first half after the first hydration break\?$")
def _(m, teams):
    return MarketSpec("timing_event", stat="goals", threshold=1, params={"window": "h1_after30"})


@_rule(r"^Will a card be shown after the second hydration break(?:, including any extra time)?\?$")
def _(m, teams):
    return MarketSpec("timing_event", stat="cards", threshold=1, params={"window": "after75"})


@_rule(r"^Will a card be shown before the first hydration break\?$")
def _(m, teams):
    return MarketSpec("timing_event", stat="cards", threshold=1, params={"window": "before30"})


@_rule(r"^Will a card be shown during first- or second-half stoppage time(?: in regulation)?\?$")
def _(m, teams):
    return MarketSpec("timing_event", stat="cards", threshold=1, params={"window": "stoppage"})


@_rule(r"^Will either team be ruled offside before the first hydration break\?$")
def _(m, teams):
    return MarketSpec("timing_event", stat="offsides", threshold=1, params={"window": "before30"})


@_rule(r"^Will (?P<n>\d+) or more corner kicks be taken before the first hydration break\?$")
def _(m, teams):
    return MarketSpec("timing_event", stat="corners", threshold=int(m["n"]),
                      params={"window": "before30"})


@_rule(r"^Will a goal be scored in (?P<half>first|second)-half stoppage(?: \(added\))? time\?$")
def _(m, teams):
    return MarketSpec("timing_event", stat="goals", threshold=1,
                      params={"window": "h1stop" if m["half"] == "first" else "h2stop"})


# --- squad-aggregate + total-shots (any player / both teams combined) ---

@_rule(r"^Will any player score (?:more than 1 goal|2 or more goals)(?: \(excluding own goals\))?\?$")
def _(m, teams):
    return MarketSpec("any_player_braces", stat="goals")


@_rule(r"^Will any player record (?P<n>\d+) or more shots on target\?$")
def _(m, teams):
    return MarketSpec("any_player_sot_ge", stat="sot", threshold=int(m["n"]))


@_rule(r"^Will any (?P<team>.+?) player (?:have|record) (?P<n>\d+) or more shots on target\?$")
def _(m, teams):
    # Team-scoped squad market (first seen POR/ESP QF): "any Portugal player 2+ SOT".
    # Without this it misparsed to a player named "any Portugal player" -> 0.15 prior.
    return MarketSpec("team_any_player_sot_ge", team=m["team"], stat="sot", threshold=int(m["n"]))


@_rule(r"^Will there be (?P<n>\d+) or more total shots \(on and off target\)\?$")
def _(m, teams):
    return MarketSpec("total_shots_ge", stat="shots", threshold=int(m["n"]))


@_rule(r"^Will a substitute score a goal(?: \(excluding own goals\))?\?$")
def _(m, teams):
    return MarketSpec("substitute_scores", stat="goals")


# 'Goal by method' families (priced as P(>=1 type | total goals), see counts.py).
@_rule(r"^Will an own goal be scored\?$")
def _(m, teams):
    return MarketSpec("own_goal", stat="goals")


@_rule(r"^Will a header goal be scored\?$")
def _(m, teams):
    return MarketSpec("header_goal", stat="goals")


@_rule(r"^Will a goal be scored from outside the (?:penalty area|box)\?$")
def _(m, teams):
    return MarketSpec("outside_box_goal", stat="goals")


@_rule(r"^Will either team make a substitution at halftime\?$")
def _(m, teams):
    return MarketSpec("substitution_at_ht")


@_rule(r"^Will a substitution be made before halftime\?$")
def _(m, teams):
    return MarketSpec("substitution_before_ht")


# Knockout-round markets re-phrase the group-stage templates with a regulation /
# extra-time qualifier ("... in regulation (90 minutes + stoppage time)", "...,
# excluding extra time", "Will regulation (90 minutes + stoppage time) end ..."),
# because KO ties can go to extra time. Our models price 90-minute regulation, so
# the qualifier is a semantic no-op — strip it to the base group-stage question.
_REG_SUFFIX = re.compile(
    r"\s+(?:in|during|of)\s+regulation\s*\(90 minutes\s*(?:\+|plus)\s*stoppage time[^)]*\)", re.I)
_REG_SUBJECT = re.compile(
    r"\bregulation\s*\(90 minutes\s*(?:\+|plus)\s*stoppage time[^)]*\)", re.I)
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
            return builder(m, teams)
    raise UnparsedQuestion(question)
