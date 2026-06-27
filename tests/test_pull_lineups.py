"""Regression guard for the 2026-06-26 stale-benched failure mode.

Covers the ESPN confirmed-lineup puller (scripts/pull_lineups.py) and the
expected_minutes precedence in PlayerModel. Deterministic — no network."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import pull_lineups as PL
from models.players import PlayerModel
from wcdata.statsbomb import build_aggregates


def _roster(*players):
    """(displayName, starter) pairs -> an ESPN-shaped team roster block."""
    return {"roster": [{"starter": st, "athlete": {"displayName": n}} for n, st in players]}


def test_verify_rejects_missing_or_partial_xi():
    """Fail-safe: write nothing unless a real XI (2 teams, >=11 starters) is posted."""
    eleven = [(f"S{i}", True) for i in range(11)]
    five = [(f"S{i}", True) for i in range(5)]
    assert PL.verify_rosters({}) is None
    assert PL.verify_rosters({"rosters": None}) is None
    assert PL.verify_rosters({"rosters": [_roster(*eleven)]}) is None            # one team
    assert PL.verify_rosters({"rosters": [_roster(*eleven), _roster(*five)]}) is None  # partial


def test_verify_accepts_full_xi():
    eleven = [(f"S{i}", True) for i in range(11)]
    assert PL.verify_rosters({"rosters": [_roster(*eleven), _roster(*eleven)]}) is not None


def test_derive_minutes_starter_bench_and_safe_unmatched():
    rosters = [_roster(("Mahmoud Trezeguet", True), ("Ahmed Sayed Zizo", False),
                       ("Mohamed Salah", True), ("Bilal Smith", True), ("Omar Smith", True)),
               _roster()]
    mins, unmatched, _ = PL.derive_minutes(
        {"Mahmoud Trezeguet", "Zizo", "Salah", "Smith", "Nobody Here"}, rosters)
    assert mins["Mahmoud Trezeguet"] == 90    # exact full-name match, starter
    assert mins["Salah"] == 90                 # unambiguous surname fallback, starter
    assert mins["Zizo"] == 20                  # surname fallback, on the bench
    # A spelling miss must NEVER become a false benching: an ambiguous surname
    # ("Smith" matches two players) and a total miss are flagged, not set to 0/20.
    assert "Smith" in unmatched and "Nobody Here" in unmatched
    assert "Smith" not in mins and "Nobody Here" not in mins


def test_pulled_minutes_override_stale_global():
    """A confirmed pull beats a stale global benching — the core of the fix."""
    _, sb = build_aggregates()
    assert PlayerModel(sb).rates("Folarin Balogun").expected_minutes == 20  # stale global
    pulled = PlayerModel(sb, match_minutes={"Folarin Balogun": 90})
    assert pulled.rates("Folarin Balogun").expected_minutes == 90


def test_default_minutes_backward_compatible():
    """No override and no pull -> 90, unchanged behaviour."""
    _, sb = build_aggregates()
    assert PlayerModel(sb).rates("Nonexistent Player XYZ").expected_minutes == 90
