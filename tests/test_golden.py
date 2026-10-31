"""The restructured code must reproduce exactly what the code that ran the
final did. Goldens were recorded from the `tournament-final` tag; see
tests/golden/make_goldens.py for how, and what each one covers."""
import json
import os
import sys
from pathlib import Path

import pytest

GOLDEN = Path(__file__).resolve().parent / "golden"
sys.path.insert(0, str(GOLDEN))
import make_goldens as mg  # noqa: E402
import scenarios as sc  # noqa: E402

# Exact by default. GOLDEN_TOL lets another CPU/BLAS accept last-digit float noise
# in the stored prices; printed output is always compared exactly.
TOL = float(os.environ.get("GOLDEN_TOL", "0"))


def _golden(rel: str):
    return json.loads((GOLDEN / "data" / rel).read_text())


def test_inputs_are_the_ones_the_goldens_used():
    expected = _golden("meta.json")["state_file_sha256"]
    assert mg.state_hashes("new", sc.REPO) == expected, (
        "data/models or data/inputs changed since the goldens were recorded — "
        "re-record them from the tournament-final tag (tests/golden/make_goldens.py)")


def test_every_board_prices_and_parses_identically(tmp_path):
    problems = mg.diff(_golden("boards.json"), mg.run_boards("new", tmp_path), tol=TOL)
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("name", list(sc.SCENARIOS))
def test_cli_scenario_is_identical(name, tmp_path):
    problems = mg.diff(_golden(f"cli/{name}.json"), mg.run_scenario("new", name, tmp_path), tol=TOL)
    assert not problems, "\n".join(problems)
