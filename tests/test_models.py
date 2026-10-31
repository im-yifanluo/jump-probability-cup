"""Structural invariants of the pricing models and the experiment mechanism."""
import importlib

import pytest

from probcup.cli import COMMANDS
from probcup.models import calibration as cal
from probcup.models.goals import ScorelineModel
from probcup.pipeline.experiment import SWAPPABLE, experimental

GRIDS = [(0.45, 0.27, 2.55), (0.74, 0.17, 3.15), (0.16, 0.22, 2.75), (0.33, 0.30, 2.2)]


@pytest.mark.parametrize("p_home,p_draw,total", GRIDS)
def test_scoreline_grid_is_a_distribution(p_home, p_draw, total):
    g = ScorelineModel.from_market(p_home, p_draw, total)
    assert g.grid.min() >= 0
    assert g.grid.sum() == pytest.approx(1.0, abs=1e-9)
    assert sum(g.total_goals_pmf()) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("p_home,p_draw,total", GRIDS)
def test_grid_reproduces_the_market_it_was_fitted_to(p_home, p_draw, total):
    g = ScorelineModel.from_market(p_home, p_draw, total)
    assert g.p_team_win("home") == pytest.approx(p_home, abs=0.01)
    assert g.p_draw() == pytest.approx(p_draw, abs=0.01)
    assert g.p_team_win("home") + g.p_draw() + g.p_team_win("away") == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("p_home,p_draw,total", GRIDS)
def test_advancement_conserves_probability(p_home, p_draw, total):
    g = ScorelineModel.from_market(p_home, p_draw, total)
    assert g.p_advance("home") + g.p_advance("away") == pytest.approx(1.0, abs=1e-9)
    assert g.p_advance("home") >= g.p_team_win("home")


def test_experiment_swaps_and_always_restores():
    before = {name: getattr(cal, name) for name in SWAPPABLE.values()}
    probe = {"timing_scale": 0.5, "total_sot_scale": 2.0,
             "tournament_intensity": {"cards": 0.5}}
    with pytest.raises(RuntimeError):
        with experimental(probe):
            assert cal.TIMING_SCALE == 0.5 and cal.TOTAL_SOT_SCALE == 2.0
            assert cal.TOURNAMENT_INTENSITY == {"cards": 0.5}
            assert cal.TOTAL_CARDS_SCALE == before["TOTAL_CARDS_SCALE"]  # untouched key
            raise RuntimeError("pricing failed mid-experiment")
    assert {name: getattr(cal, name) for name in SWAPPABLE.values()} == before


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_every_cli_command_has_a_main(command):
    module = importlib.import_module(COMMANDS[command][0])
    assert callable(module.main)
