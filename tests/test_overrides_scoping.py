"""scorer_prop_prob must be SCOPED to the match it was researched for, so an
opponent-specific price can't silently ride into the team's next game."""
import pathlib
import sys
import tempfile

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
import price_match as pm  # noqa: E402


def _overrides_file(d) -> pathlib.Path:
    tf = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False)
    yaml.safe_dump(d, tf)
    tf.close()
    return pathlib.Path(tf.name)


def test_scorer_prop_scoping(monkeypatch):
    ovr = {
        "A": {"scorer_prop_prob": 0.40, "scorer_prop_match": "FRA vs SWE"},
        "B": {"scorer_prop_prob": 0.30, "scorer_prop_match": "GER vs PAR"},
        "C": {"scorer_prop_prob": 0.20},  # untagged
    }
    monkeypatch.setattr(pm, "OVERRIDES_PATH", _overrides_file(ovr))
    # scoped to FRA vs SWE: the matching tag is used, the other expires, untagged still applies
    assert pm._scorer_props_from_overrides("FRA vs SWE") == {"A": 0.40, "C": 0.20}
    # a different match: A now expires, B matches
    assert pm._scorer_props_from_overrides("GER vs PAR") == {"B": 0.30, "C": 0.20}
    # legacy call (no match_name) returns everything — backward compatible
    assert pm._scorer_props_from_overrides() == {"A": 0.40, "B": 0.30, "C": 0.20}
