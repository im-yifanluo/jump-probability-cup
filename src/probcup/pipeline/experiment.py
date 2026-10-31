"""Bot 2's live A/B experiment: the same odds, a different set of constants.

data/inputs/bot2_experiment.yaml names an experiment and overrides any of the
swappable calibration constants below. Every run prices each match once for
bot 1 (the stable configuration), then reprices the same markets for bot 2
inside `experimental(...)`, which swaps the constants and always restores
them. Identical odds and inputs isolate the strategy as the only difference.

A probe that wins ~8-10 of its own settled markets is promoted to bot 1's
constants in calibration.py; a clear loser is retired (see docs/strategy.md §8).
"""
from __future__ import annotations

import contextlib

import yaml

from probcup import config
from probcup.models import calibration as cal

EXPERIMENT_PATH = config.BOT2_EXPERIMENT

# experiment key -> calibration constant it overrides
SWAPPABLE = {
    "tournament_intensity": "TOURNAMENT_INTENSITY",
    "tournament_team_sot": "TOURNAMENT_TEAM_SOT",
    "total_sot_scale": "TOTAL_SOT_SCALE",
    "total_offsides_scale": "TOTAL_OFFSIDES_SCALE",
    "total_shots_scale": "TOTAL_SHOTS_SCALE",
    "total_cards_scale": "TOTAL_CARDS_SCALE",
    "favorite_sot_boost": "FAVORITE_SOT_BOOST",
    "timing_scale": "TIMING_SCALE",
}


def load_experiment(bots: list[int]) -> dict | None:
    """Bot 2's experiment from data/inputs/bot2_experiment.yaml, or None — so
    bot 2 MIRRORS bot 1 — if the file is absent/empty, sets no swappable key,
    or bot 2 isn't configured."""
    if 2 not in bots or not EXPERIMENT_PATH.exists():
        return None
    exp = yaml.safe_load(EXPERIMENT_PATH.read_text()) or {}
    return exp if any(exp.get(k) for k in SWAPPABLE) else None


@contextlib.contextmanager
def experimental(exp: dict):
    """Temporarily swap the live calibration constants for the experiment's
    around a second pricing pass. The models read `cal.NAME` at call time, so
    the SAME pricer reprices under the override. Always restored, even on error."""
    saved = {name: getattr(cal, name) for name in SWAPPABLE.values()}
    try:
        for key, name in SWAPPABLE.items():
            if exp.get(key):
                value = exp[key]
                setattr(cal, name, value if key == "tournament_intensity" else float(value))
        yield
    finally:
        for name, value in saved.items():
            setattr(cal, name, value)
