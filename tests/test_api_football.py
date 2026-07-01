import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from wcdata.api_football import _fold, country_matches, profile_is_acceptable_match, profile_match_score


def test_profile_match_score_prefers_expanded_first_last_name():
    wrong = {
        "name": "N. Ueda",
        "firstname": "Naomichi Ueda",
        "lastname": None,
    }
    right = {
        "name": "A. Ueda",
        "firstname": "Ayase Ueda",
        "lastname": None,
    }

    assert profile_match_score("Ayase Ueda", right) > profile_match_score("Ayase Ueda", wrong)


def test_fold_transliterates_scandinavian_player_names():
    assert _fold("Alexander Sørloth") == "alexander sorloth"
    assert _fold("Martin Ødegaard") == "martin odegaard"


def test_country_matches_accepts_world_cup_aliases():
    assert country_matches("Côte d'Ivoire", {"Ivory Coast"})
    assert country_matches("USA", {"United States"})
    assert country_matches("Bosnia", {"Bosnia and Herzegovina"})


def test_profile_match_rejects_near_name_surname_hit():
    wrong = {
        "name": "Amadou Diallo",
        "firstname": "Amadou",
        "lastname": "Diallo",
    }
    right = {
        "name": "A. Diallo",
        "firstname": "Amad Diallo",
        "lastname": None,
    }

    assert not profile_is_acceptable_match("Amad Diallo", wrong)
    assert profile_is_acceptable_match("Amad Diallo", right)
