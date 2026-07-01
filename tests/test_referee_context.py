import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from price_match import referee_context


def test_referee_context_caps_strict_referee_at_one():
    ctx = referee_context("GER vs CIV")

    assert ctx["status"] == "override"
    assert ctx["cards_per_game"] > 3.46
    assert ctx["card_factor"] == 1.0


def test_referee_context_unknown_match_is_neutral():
    ctx = referee_context("RSA vs CAN")

    assert ctx["status"] == "no_override"
    assert ctx["card_factor"] == 1.0

