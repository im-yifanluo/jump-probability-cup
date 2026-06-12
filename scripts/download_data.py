"""Download all raw datasets into data/raw/ (idempotent — skips existing files).

Sources:
- football-data.co.uk club CSVs (goals/HT, corners, cards, SOT + closing odds)
- martj42 international results
- StatsBomb open-data: international tournaments (matches, lineups, events)

Run:  python3 scripts/download_data.py            # everything
      python3 scripts/download_data.py --no-events  # skip the big event files
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests
from tqdm import tqdm

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"

FDUK_URL = "https://www.football-data.co.uk/mmz4281/{season}/{div}.csv"
# Two-digit season pairs 2009/10 .. 2025/26 (corner/card/SOT columns are
# reliable from ~2005, odds coverage best from 2009 on).
FDUK_SEASONS = [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(2009, 2026)]
FDUK_DIVISIONS = [
    "E0", "E1", "E2", "E3",        # England
    "SC0", "SC1",                  # Scotland
    "D1", "D2",                    # Germany
    "I1", "I2",                    # Italy
    "SP1", "SP2",                  # Spain
    "F1", "F2",                    # France
    "N1", "B1", "P1", "T1", "G1",  # NL, BE, PT, TR, GR
]

INTERNATIONALS_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"

SB_BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
# (competition_id, season_id, slug) — men's international tournaments
SB_TOURNAMENTS = [
    (43, 106, "wc2022"),
    (43, 3, "wc2018"),
    (55, 282, "euro2024"),
    (55, 43, "euro2020"),
    (223, 282, "copa2024"),
    (1267, 107, "afcon2023"),
]


def fetch(url: str, dest: Path, session: requests.Session) -> bool:
    """Download url -> dest unless it already exists. Returns True if fetched."""
    if dest.exists():
        return False
    resp = session.get(url, timeout=30)
    if resp.status_code == 404:  # some season/division combos don't exist
        return False
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return True


def download_football_data_uk(session: requests.Session) -> None:
    jobs = [(s, d) for s in FDUK_SEASONS for d in FDUK_DIVISIONS]
    for season, div in tqdm(jobs, desc="football-data.co.uk"):
        if fetch(FDUK_URL.format(season=season, div=div),
                 RAW / "fduk" / f"{season}_{div}.csv", session):
            time.sleep(0.2)  # polite rate


def download_internationals(session: requests.Session) -> None:
    fetch(INTERNATIONALS_URL, RAW / "internationals" / "results.csv", session)
    print("internationals: done")


def download_statsbomb(session: requests.Session, include_events: bool) -> None:
    for comp_id, season_id, slug in SB_TOURNAMENTS:
        matches_path = RAW / "statsbomb" / slug / "matches.json"
        fetch(f"{SB_BASE}/matches/{comp_id}/{season_id}.json", matches_path, session)
        matches = json.loads(matches_path.read_text())
        print(f"statsbomb {slug}: {len(matches)} matches")
        if not include_events:
            continue
        for match in tqdm(matches, desc=f"events {slug}"):
            mid = match["match_id"]
            if fetch(f"{SB_BASE}/events/{mid}.json",
                     RAW / "statsbomb" / slug / "events" / f"{mid}.json", session):
                time.sleep(0.1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-events", action="store_true",
                        help="skip per-match StatsBomb event files (the bulk of the download)")
    args = parser.parse_args()

    session = requests.Session()
    session.headers["User-Agent"] = "probability-cup-research/1.0"

    download_internationals(session)
    download_football_data_uk(session)
    download_statsbomb(session, include_events=not args.no_events)
    print("All downloads complete.")


if __name__ == "__main__":
    main()
