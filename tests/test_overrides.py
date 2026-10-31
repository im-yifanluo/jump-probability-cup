"""scorer_prop_prob must be SCOPED to the match it was researched for, so an
opponent-specific price can't silently ride into the team's next game."""
import yaml

from probcup.pipeline import overrides


def test_scorer_prop_scoping(monkeypatch, tmp_path):
    ovr = {
        "A": {"scorer_prop_prob": 0.40, "scorer_prop_match": "FRA vs SWE"},
        "B": {"scorer_prop_prob": 0.30, "scorer_prop_match": "GER vs PAR"},
        "C": {"scorer_prop_prob": 0.20},  # untagged
    }
    path = tmp_path / "player_overrides.yaml"
    path.write_text(yaml.safe_dump(ovr))
    monkeypatch.setattr(overrides, "PLAYER_OVERRIDES", path)
    # scoped to FRA vs SWE: the matching tag is used, the other expires, untagged still applies
    assert overrides.scorer_props("FRA vs SWE") == {"A": 0.40, "C": 0.20}
    # a different match: A now expires, B matches
    assert overrides.scorer_props("GER vs PAR") == {"B": 0.30, "C": 0.20}
    # legacy call (no match_name) returns everything — backward compatible
    assert overrides.scorer_props() == {"A": 0.40, "B": 0.30, "C": 0.20}
