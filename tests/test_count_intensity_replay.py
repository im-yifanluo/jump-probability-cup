import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from count_intensity_replay import candidate_maps, canonical_team, split_match


def test_candidate_maps_are_predeclared_and_named():
    maps = candidate_maps()

    assert maps["current_v4"]["cards"] == 0.78
    assert maps["sot105_only"] == {
        "cards": 0.78,
        "sot": 1.05,
        "corners": 1.05,
        "offsides": 0.90,
        "fouls": 0.90,
    }
    assert maps["uniform090_v3"]["sot"] == 0.90
    assert maps["aggressive_bot2"]["corners"] == 1.15
    assert maps["no_extra_100"]["offsides"] == 1.00


def test_replay_team_canonicalization_handles_history_names():
    assert canonical_team("Cabo Verde") == "Cape Verde"
    assert canonical_team("Côte d'Ivoire") == "Ivory Coast"
    assert canonical_team("IR Iran") == "Iran"
    assert split_match("Switzerland vs Bosnia-Herzegovina") == (
        "Switzerland",
        "Bosnia and Herzegovina",
    )
