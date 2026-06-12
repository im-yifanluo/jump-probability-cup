"""Populate data/player_overrides.yaml with API-Football season rates for the
players named in upcoming prop markets.

Converts raw season stats into the override fields PlayerModel consumes:
  sot_per_match      -> used directly (match-tempo scaled at pricing time)
  goal_share         -> goals_per_match / typical team goals (~1.45 club)
  involvement_share  -> involvements_per_match / same denominator

Usage:
    python3 scripts/update_player_rates.py                 # players in all open markets
    python3 scripts/update_player_rates.py "CAN vs BIH"    # one match only
Existing override entries are updated, hand-written keys are preserved.
Budget: 1 API request per player (free plan: 100/day).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import yaml

from probability_cup import ProbabilityCup
from questions.parser import parse
from questions.teams import ALL_TEAM_NAMES, match_teams
from wcdata.api_football import ApiFootball

OVERRIDES = ROOT / "data" / "player_overrides.yaml"
CLUB_TEAM_GOALS = 1.45  # typical club-season team goals/match, share denominator


def main():
    match_filter = sys.argv[1] if len(sys.argv) > 1 else None

    markets = ProbabilityCup(bot=1).markets_df()
    if match_filter:
        markets = markets[markets["match_name"] == match_filter]
    players: dict[str, set[str]] = {}  # player -> candidate nationalities
    for _, row in markets.iterrows():
        spec = parse(row["question"], teams=ALL_TEAM_NAMES)
        if spec.player:
            players.setdefault(spec.player, set()).update(match_teams(row["match_name"]))

    api = ApiFootball()
    budget = api.remaining_today()
    print(f"{len(players)} players to look up (2 requests each), {budget} API requests left today")
    if 2 * len(players) + 2 > budget:
        raise SystemExit("Not enough request budget — pass a match name to narrow down.")

    overrides = yaml.safe_load(OVERRIDES.read_text()) if OVERRIDES.exists() else {}
    overrides = overrides or {}
    MIN_APPS = 10  # below this, season rates are noise (injury years etc.)
    for name, nationalities in sorted(players.items()):
        rates = api.player_season_rates(name, nationalities)
        if rates is None:
            print(f"  {name:28s} -> not found (keeping prior/manual values)")
            continue
        if rates["apps"] < MIN_APPS:
            print(f"  {name:28s} -> only {rates['apps']} apps in {rates['season']} "
                  f"(injury/transfer year?) — NOT overriding, research manually")
            overrides.pop(name, None)  # drop any earlier low-sample write
            continue
        entry = overrides.setdefault(name, {})
        entry.update({
            "sot_per_match": rates["sot_per_match"],
            "goal_share": round(rates["goals_per_match"] / CLUB_TEAM_GOALS, 3),
            "involvement_share": round(rates["involvements_per_match"] / CLUB_TEAM_GOALS, 3),
            "source": f"api-football {rates['season']} ({rates['apps']} apps, "
                      f"matched {rates['matched_name']})",
        })
        print(f"  {name:28s} -> SOT {rates['sot_per_match']:.2f}/match, "
              f"goals {rates['goals_per_match']:.2f}, apps {rates['apps']}")

    OVERRIDES.write_text(yaml.safe_dump(overrides, allow_unicode=True, sort_keys=True))
    print(f"\nwrote {OVERRIDES}")


if __name__ == "__main__":
    main()
