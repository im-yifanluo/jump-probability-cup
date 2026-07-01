"""Pull confirmed starting XIs from ESPN's free public API -> expected_minutes.

Replaces hand-parsed web lineups (the source of the 06-26 Trezeguet miss: a stale
benched override a garbled scrape failed to revert). ESPN's match `summary`
endpoint (the same one we use for odds) carries a `rosters` block once the XI is
posted (~60 min pre-kickoff); each player has `starter`, `active`, and
`athlete.displayName`. We map our market players onto it:

    starter             -> 90
    in squad, on bench  -> 20   (possible cameo)
    NOT matched         -> left at default + FLAGGED (never forced to 0, so a
                           spelling miss can't manufacture a false benching)

ROBUST BY CONSTRUCTION — it writes NOTHING unless ALL of:
  * the ESPN event's two teams orient to our match,
  * a real XI is posted (exactly 2 rosters, >=11 starters each).
So an early/missing/partial lineup leaves the all-90 default untouched rather
than guessing. The result is written per-match (scoped, self-expiring) to
data/match_lineups.yaml; build_pricer reads only the entry for the match it
prices, so a benching can never leak into a later fixture.

    python scripts/pull_lineups.py "EGY vs IRN"            # pull, verify, write
    python scripts/pull_lineups.py "EGY vs IRN" --dry-run  # report only
"""
from __future__ import annotations

import argparse
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import requests
import yaml

from models.players import _normalize_name
from questions.parser import parse
from questions.teams import ALL_TEAM_NAMES, match_teams, names_match
from wcdata.odds_feeds import SCOREBOARD, SUMMARY

MATCH_LINEUPS = ROOT / "data" / "match_lineups.yaml"
STARTER_MINUTES = 90
BENCH_MINUTES = 20
MIN_STARTERS = 11   # a posted XI; below this the lineup isn't really out yet
REQUEST_TIMEOUT_SECONDS = 20
REQUEST_ATTEMPTS = 3
RETRY_BASE_SLEEP_SECONDS = 0.5


class SourceFetchError(RuntimeError):
    """Raised when ESPN can be reached only unreliably, after bounded retries."""


def _get_json_with_retries(session: requests.Session, url: str, *, params: dict) -> dict:
    last_exc: BaseException | None = None
    for attempt in range(1, REQUEST_ATTEMPTS + 1):
        try:
            response = session.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            raise_for_status = getattr(response, "raise_for_status", None)
            if raise_for_status:
                raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            if attempt == REQUEST_ATTEMPTS:
                break
            time.sleep(RETRY_BASE_SLEEP_SECONDS * (2 ** (attempt - 1)))
        except ValueError as exc:
            raise SourceFetchError(f"ESPN returned invalid JSON for {url} params={params}: {exc}") from exc
    raise SourceFetchError(
        f"ESPN request failed after {REQUEST_ATTEMPTS} attempts for {url} "
        f"params={params}: {type(last_exc).__name__}: {last_exc}"
    ) from last_exc


def _fold(name: str) -> str:
    """ascii-folded, lowercased, hyphens->spaces (token ORDER preserved, unlike
    _normalize_name which sorts) — so the last token is the surname."""
    return (unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
            .lower().replace("-", " "))


def _squash_repeats(value: str) -> str:
    """Collapse repeated letters for conservative ESPN/name spelling variants.

    This catches cases like Al-Taamari vs Al-Tamari after exact matching has
    failed. It is only used as a fallback over already-oriented match rosters.
    """
    out = []
    for ch in value:
        if not out or out[-1] != ch:
            out.append(ch)
    return "".join(out)


def _find_event_summary(match_name: str, kickoff, session: requests.Session) -> dict:
    """ESPN summary JSON for our match. Raises LookupError if the fixture can't
    be found or its two teams don't orient to our match (fail-safe -> no write)."""
    home_full, away_full = match_teams(match_name)
    hint = home_full.split()[0].lower()
    day = kickoff.strftime("%Y%m%d")
    prev = (kickoff - timedelta(days=1)).strftime("%Y%m%d")
    event = None
    for d in (day, prev):  # ESPN buckets by US local date; late-UTC sits on prev day
        board = _get_json_with_retries(session, SCOREBOARD, params={"dates": d})
        event = next((e for e in board.get("events", []) if hint in e["name"].lower()), None)
        if event:
            break
    if event is None:
        raise LookupError(f"no ESPN fixture matching {match_name!r}")
    espn_teams = [c["team"]["displayName"] for c in event["competitions"][0]["competitors"]]
    if not (any(names_match(home_full, t) for t in espn_teams)
            and any(names_match(away_full, t) for t in espn_teams)):
        raise LookupError(f"ESPN teams {espn_teams} don't orient to {match_name!r}")
    return _get_json_with_retries(session, SUMMARY, params={"event": event["id"]})


def verify_rosters(summary: dict) -> list | None:
    """Return the rosters ONLY if a real XI is posted: exactly 2 teams, each with
    at least 11 starters. Otherwise None (lineups not out — keep defaults)."""
    rosters = summary.get("rosters")
    if not rosters or len(rosters) != 2:
        return None
    for team in rosters:
        if sum(1 for p in team.get("roster", []) if p.get("starter")) < MIN_STARTERS:
            return None
    return rosters


def _index(rosters: list) -> tuple[dict, dict]:
    """Build (full-name -> (display, starter)) and (surname -> [(display, starter)])
    lookups over both squads, for conservative name matching."""
    full, surname = {}, {}
    for team in rosters:
        for p in team.get("roster", []):
            disp = p.get("athlete", {}).get("displayName") or p.get("athlete", {}).get("fullName")
            if not disp:
                continue
            rec = (disp, bool(p.get("starter")))
            full[_normalize_name(disp)] = rec
            toks = _fold(disp).split()
            if toks:
                surname.setdefault(toks[-1], []).append(rec)
    return full, surname


def _match(name: str, full: dict, surname: dict):
    """(display, starter) for our player, or None if not CONFIDENTLY matched.
    Exact (order-insensitive) full name first, then an UNAMBIGUOUS surname hit."""
    rec = full.get(_normalize_name(name))
    if rec:
        return rec
    squashed = _squash_repeats(_normalize_name(name))
    squashed_hits = [rec for key, rec in full.items() if _squash_repeats(key) == squashed]
    if len(squashed_hits) == 1:
        return squashed_hits[0]
    toks = _fold(name).split()
    if toks:
        cands = surname.get(toks[-1], [])
        if len(cands) == 1:
            return cands[0]
        squashed_surname = _squash_repeats(toks[-1])
        squashed_cands = [
            rec
            for key, values in surname.items()
            if _squash_repeats(key) == squashed_surname
            for rec in values
        ]
        if len(squashed_cands) == 1:
            return squashed_cands[0]
    return None


def derive_minutes(our_players, rosters: list) -> tuple[dict, list, list]:
    """-> (minutes {player: 90|20}, unmatched [player], detail [(player, espn, role, min)])."""
    full, surname = _index(rosters)
    minutes, unmatched, detail = {}, [], []
    for p in sorted(our_players):
        rec = _match(p, full, surname)
        if rec is None:
            unmatched.append(p)
            continue
        disp, starter = rec
        minutes[p] = STARTER_MINUTES if starter else BENCH_MINUTES
        detail.append((p, disp, "START" if starter else "bench", minutes[p]))
    return minutes, unmatched, detail


def _market_players(match_name: str) -> set[str]:
    """The player names that appear in our markets for this match (spec.player) —
    the only names whose expected_minutes matters and the exact keys rates() uses."""
    from probability_cup import ProbabilityCup
    mine = ProbabilityCup(bot=1).markets_df()
    mine = mine[mine["match_name"] == match_name]
    players = set()
    for q in mine["question"]:
        try:
            spec = parse(q, teams=ALL_TEAM_NAMES)
            if spec.player:
                players.add(spec.player)
        except Exception:
            pass
    return players


def pull_lineup(match_name: str, kickoff, players=None,
                session: requests.Session | None = None) -> dict:
    """Pull + verify + derive (no write).

    status in {pulled, not_posted, no_event, source_error}.
    """
    s = session or requests.Session()
    if players is None:
        players = _market_players(match_name)
    try:
        summary = _find_event_summary(match_name, kickoff, s)
    except SourceFetchError as exc:
        return {"status": "source_error", "error": str(exc),
                "minutes": {}, "unmatched": sorted(players), "detail": []}
    except LookupError as exc:
        return {"status": "no_event", "error": str(exc),
                "minutes": {}, "unmatched": sorted(players), "detail": []}
    rosters = verify_rosters(summary)
    if rosters is None:
        return {"status": "not_posted", "minutes": {},
                "unmatched": sorted(players), "detail": []}
    minutes, unmatched, detail = derive_minutes(players, rosters)
    return {"status": "pulled", "minutes": minutes,
            "unmatched": unmatched, "detail": detail}


def write_lineup(match_name: str, kickoff, result: dict) -> None:
    """Persist the per-match entry (scoped by match name, timestamped)."""
    data = (yaml.safe_load(MATCH_LINEUPS.read_text()) or {}) if MATCH_LINEUPS.exists() else {}
    data[match_name] = {
        "pulled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kickoff": kickoff.isoformat() if hasattr(kickoff, "isoformat") else str(kickoff),
        "source": "espn",
        "status": result["status"],
        "minutes": result["minutes"],
        "unmatched": result["unmatched"],
    }
    MATCH_LINEUPS.parent.mkdir(parents=True, exist_ok=True)
    MATCH_LINEUPS.write_text(yaml.safe_dump(data, sort_keys=True, allow_unicode=True))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("match", help='match name as in the snapshot, e.g. "EGY vs IRN"')
    ap.add_argument("--dry-run", action="store_true", help="report only, do not write")
    args = ap.parse_args()

    from probability_cup import ProbabilityCup
    mine = ProbabilityCup(bot=1).markets_df()
    mine = mine[mine["match_name"] == args.match]
    if mine.empty:
        raise SystemExit(f"no open markets for {args.match!r}")
    kickoff = mine["kickoff"].iloc[0]

    res = pull_lineup(args.match, kickoff)
    print(f"[{res['status']}] {args.match}  (kickoff {kickoff:%Y-%m-%d %H:%M UTC})")
    for player, espn, role, mins in res["detail"]:
        tag = "" if _normalize_name(player) == _normalize_name(espn) else f"   (ESPN: {espn})"
        print(f"  {mins:>3}  {role:5}  {player}{tag}")
    if res["unmatched"]:
        print("  UNMATCHED (left at default 90, verify manually):", ", ".join(res["unmatched"]))
    if res["status"] != "pulled":
        print("  -> lineups not posted / no event — NOTHING written, all-90 default kept")
    elif args.dry_run:
        print("  -> dry run, nothing written")
    else:
        write_lineup(args.match, kickoff, res)
        print(f"  -> wrote data/{MATCH_LINEUPS.name}[{args.match!r}]")


if __name__ == "__main__":
    main()
