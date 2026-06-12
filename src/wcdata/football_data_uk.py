"""Loader for football-data.co.uk club CSVs -> one tidy match DataFrame.

Columns we rely on (see football-data.co.uk/notes.txt):
  FTHG/FTAG, HTHG/HTAG       full-time / half-time goals
  HC/AC                      corners
  HY/AY, HR/AR               yellow / red cards
  HST/AST                    shots on target
  HF/AF                      fouls
  PSH/PSD/PSA (or B365*/Avg*) closing 1X2 odds
  P>2.5 / P<2.5 (or B365/Avg) over-under 2.5 goals odds

The cleaned frame is cached to data/club_matches.parquet.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent.parent
RAW_DIR = ROOT / "data" / "raw" / "fduk"
CACHE = ROOT / "data" / "club_matches.parquet"

STAT_COLUMNS = {
    "FTHG": "home_goals", "FTAG": "away_goals",
    "HTHG": "home_goals_1h", "HTAG": "away_goals_1h",
    "HC": "home_corners", "AC": "away_corners",
    "HST": "home_sot", "AST": "away_sot",
    "HF": "home_fouls", "AF": "away_fouls",
}

# Closing-odds preference order: Pinnacle (sharpest), bet365, market average.
ODDS_1X2 = [("PSH", "PSD", "PSA"), ("B365H", "B365D", "B365A"), ("AvgH", "AvgD", "AvgA")]
ODDS_OU = [("P>2.5", "P<2.5"), ("B365>2.5", "B365<2.5"), ("Avg>2.5", "Avg<2.5")]


def _pick_odds(df: pd.DataFrame, candidates: list[tuple], names: list[str]) -> pd.DataFrame:
    """First non-null odds source per row, in preference order."""
    out = pd.DataFrame(index=df.index, columns=names, dtype=float)
    for cols in candidates:
        if all(c in df.columns for c in cols):
            block = df[list(cols)].apply(pd.to_numeric, errors="coerce")
            mask = out[names[0]].isna() & block.notna().all(axis=1)
            out.loc[mask, names] = block.loc[mask].to_numpy()
    return out


def _load_one(path: Path) -> pd.DataFrame | None:
    try:
        raw = pd.read_csv(path, encoding="latin-1", on_bad_lines="skip")
    except Exception:
        return None
    if "FTHG" not in raw.columns or raw.empty:
        return None

    df = pd.DataFrame({
        "season": path.stem.split("_")[0],
        "league": raw.get("Div"),
        "date": pd.to_datetime(raw["Date"], format="mixed", dayfirst=True, errors="coerce"),
        "home_team": raw["HomeTeam"],
        "away_team": raw["AwayTeam"],
    })
    for src, dst in STAT_COLUMNS.items():
        df[dst] = pd.to_numeric(raw.get(src), errors="coerce")
    # Cards = yellows + reds per side
    for side, y, r in (("home", "HY", "HR"), ("away", "AY", "AR")):
        df[f"{side}_cards"] = (pd.to_numeric(raw.get(y), errors="coerce")
                               + pd.to_numeric(raw.get(r), errors="coerce"))
    df[["odds_home", "odds_draw", "odds_away"]] = _pick_odds(
        raw, ODDS_1X2, ["odds_home", "odds_draw", "odds_away"])
    df[["odds_over25", "odds_under25"]] = _pick_odds(
        raw, ODDS_OU, ["odds_over25", "odds_under25"])
    return df.dropna(subset=["date", "home_team", "home_goals", "away_goals"])


def load_club_matches(refresh: bool = False) -> pd.DataFrame:
    """All club matches, cleaned and cached. ~70k rows with full stats+odds."""
    if CACHE.exists() and not refresh:
        return pd.read_parquet(CACHE)
    frames = [df for path in sorted(RAW_DIR.glob("*.csv"))
              if (df := _load_one(path)) is not None]
    matches = pd.concat(frames, ignore_index=True).sort_values("date").reset_index(drop=True)
    matches.to_parquet(CACHE)
    return matches
