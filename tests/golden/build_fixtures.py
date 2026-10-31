"""One-time builder for the golden-test fixtures (outputs are committed).

Reconstructs every real match board from the platform's settled-results export
(results/data/settled_markets_bot*.csv: market id, question, match id) and
attaches a platform-style match name ("ESP vs ARG"), so the pricer resolves
teams, lineups, referees and scorer props exactly as it did live.

Team order (first-listed = "home" for the pricer) comes from, in priority:
the crowd ledger's match names (data/cache/rbp_full.csv, local only), the
match names used as keys in data/inputs/*.yaml, then the questions themselves.

Odds are synthetic but realistic (a fixed cycle of favourite / even /
underdog profiles): the goldens test that the code is unchanged, not that the
odds were right.

Also writes:
  platform_snapshot.json  a real 13-match / 175-market board (2026-06-27)
                          used by the pipeline scenarios
  experiment_probe.yaml   a bot-2 experiment that moves every swappable knob

Inputs that only exist on the original machine: data/cache/rbp_full.csv and
../data/snapshot.json (a 2026-06-27 snapshot copy). Re-running is never needed.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures"

try:  # after the restructure
    from probcup.questions.teams import CODE_TO_NAME, names_match
except ImportError:  # the original flat layout
    sys.path.insert(0, str(ROOT / "src"))
    from questions.teams import CODE_TO_NAME, names_match

NAME_TO_CODE = {v: k for k, v in CODE_TO_NAME.items()}
TEAMS = sorted(CODE_TO_NAME.values(), key=len, reverse=True)  # longest first

# p_home (first-listed team), p_draw, expected total goals
PROFILES = [(0.45, 0.27, 2.55), (0.62, 0.22, 2.85), (0.24, 0.28, 2.35),
            (0.36, 0.30, 2.45), (0.74, 0.17, 3.15), (0.16, 0.22, 2.75),
            (0.52, 0.26, 2.65), (0.30, 0.31, 2.20)]


def _first_existing(*paths: Path) -> Path | None:
    return next((p for p in paths if p.exists()), None)


def _teams_in(questions: list[str]) -> list[str]:
    """Canonical team names mentioned anywhere in a board's questions."""
    found = set()
    for q in questions:
        rest = q
        for t in TEAMS:  # longest first so "South Korea" wins over a substring
            if t in rest:
                found.add(t)
                rest = rest.replace(t, " ")
    return sorted(found)


def _canonical(display: str, candidates: list[str]) -> str | None:
    hits = [c for c in candidates if names_match(c, display)]
    return hits[0] if len(hits) == 1 else None


def main() -> None:
    res = pd.concat([pd.read_csv(ROOT / "results" / "data" / f"settled_markets_bot{b}.csv")
                     for b in (1, 2)])
    res = res.drop_duplicates("market_id")

    ledger_path = _first_existing(ROOT / "data" / "cache" / "rbp_full.csv",
                                  ROOT / "data" / "rbp_full.csv")
    q_to_display: dict[str, str] = {}
    if ledger_path:
        led = pd.read_csv(ledger_path)
        q_to_display = dict(zip(led["question"].str.strip(), led["match"]))

    inputs = _first_existing(ROOT / "data" / "inputs", ROOT / "data")
    known_names: set[str] = set()
    kickoffs: dict[str, str] = {}
    for f in ("referee_overrides.yaml", "match_lineups.yaml"):
        d = yaml.safe_load((inputs / f).read_text()) or {}
        known_names |= set(d)
        if f == "match_lineups.yaml":
            kickoffs |= {k: v.get("kickoff") for k, v in d.items() if v.get("kickoff")}

    boards, unresolved = [], []
    for match_id, grp in res.groupby("match_id", sort=False):
        questions = grp["question"].str.strip().tolist()
        teams = _teams_in(questions)
        order = None
        displays = Counter(q_to_display[q] for q in questions if q in q_to_display)
        if displays and len(teams) == 2:
            left, _, right = displays.most_common(1)[0][0].partition(" vs ")
            a, b = _canonical(left, teams), _canonical(right, teams)
            if a and b and a != b:
                order = (a, b)
        if order is None and len(teams) == 2:
            for name in sorted(known_names):
                left, _, right = name.partition(" vs ")
                pair = (CODE_TO_NAME.get(left), CODE_TO_NAME.get(right))
                if set(pair) == set(teams):
                    order = pair
                    break
        if order is None and len(teams) == 2:
            order = tuple(teams)
            unresolved.append(f"{teams} (order guessed)")
        if order is None:
            unresolved.append(f"{match_id}: teams {teams} — skipped")
            continue
        name = f"{NAME_TO_CODE[order[0]]} vs {NAME_TO_CODE[order[1]]}"
        first = grp["created_date"].min()
        boards.append({
            "match_id": match_id,
            "match_name": name,
            "kickoff": kickoffs.get(name),
            "first_created": first,
            "markets": [{"market_id": m, "question": q.strip()}
                        for m, q in zip(grp["market_id"], grp["question"])],
        })
    boards.sort(key=lambda b: (b["first_created"], b["match_name"]))

    odds = {}
    for i, b in enumerate(boards):
        p_home, p_draw, total = PROFILES[i % len(PROFILES)]
        odds.setdefault(b["match_name"], {"p_home": p_home, "p_draw": p_draw,
                                          "total_goals": total})

    FIX.mkdir(parents=True, exist_ok=True)
    (ROOT / "examples" / "boards.json").write_text(json.dumps(boards, indent=1, ensure_ascii=False))
    (FIX / "odds.yaml").write_text(yaml.safe_dump(odds, sort_keys=True, allow_unicode=True))

    snap_src = ROOT.parent / "data" / "snapshot.json"
    if snap_src.exists():
        snap = json.loads(snap_src.read_text())
        (FIX / "platform_snapshot.json").write_text(json.dumps(snap, indent=1, ensure_ascii=False))

    probe = {
        "name": "golden-probe",
        "description": "synthetic bot-2 experiment that moves every swappable knob "
                       "(golden tests only)",
        "tournament_intensity": {"cards": 0.72, "sot": 1.05, "corners": 1.15,
                                 "offsides": 0.9, "fouls": 0.9},
        "tournament_team_sot": 4.3155,
        "favorite_sot_boost": 0.10,
        "total_cards_scale": 0.80,
        "total_sot_scale": 1.60,
        "total_offsides_scale": 1.20,
        "total_shots_scale": 1.10,
        "timing_scale": 0.70,
    }
    (FIX / "experiment_probe.yaml").write_text(yaml.safe_dump(probe, sort_keys=False))

    n_mk = sum(len(b["markets"]) for b in boards)
    print(f"{len(boards)} boards, {n_mk} markets; {len(set(b['match_name'] for b in boards))} names")
    for u in unresolved:
        print("  NOTE:", u)


if __name__ == "__main__":
    main()
