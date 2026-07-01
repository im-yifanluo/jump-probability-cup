import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from referee_research_audit import decision, extract_summary_officials, is_card_market, official_like_paths


def test_extract_summary_officials_from_header_competition():
    summary = {
        "header": {
            "competitions": [
                {
                    "officials": [
                        {"displayName": "Jane Doe", "position": {"name": "Referee"}},
                        {"fullName": "Alex Assistant"},
                    ]
                }
            ]
        }
    }

    assert extract_summary_officials(summary) == ["Jane Doe", "Alex Assistant"]


def test_official_like_paths_find_nested_keys():
    summary = {"header": {"competitions": [{"officials": []}]}, "gameInfo": {"referee": "Jane Doe"}}

    assert "$.header.competitions[0].officials" in official_like_paths(summary)
    assert "$.gameInfo.referee" in official_like_paths(summary)


def test_card_market_detection_covers_yellow_and_red_rows():
    assert is_card_market("Will there be 4 or more total cards shown?")
    assert is_card_market("Will a red card be shown in the match?")
    assert not is_card_market("Will there be 9 or more total corner kicks?")


def test_decision_requires_research_before_new_referee_override():
    assert decision("no_override", ["Jane Doe"], 1) == "needs_referee_card_rate_research_before_override"
    assert decision("no_override", [], 1) == "no_verified_referee; keep neutral card_factor=1.00; field is unavailable before lock"
    assert decision("override", ["Jane Doe"], 1) == "use_existing_capped_override"
    assert decision("no_override", [], 0) == "no_open_card_markets"
