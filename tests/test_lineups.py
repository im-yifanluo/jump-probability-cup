"""Regression guard for the 2026-06-26 stale-benched failure mode.

Covers the ESPN confirmed-lineup puller (probcup.sources.espn_lineups) and the
expected_minutes precedence in PlayerModel. Deterministic — no network."""
import probcup.sources.espn_lineups as PL
from probcup.models.players import PlayerModel
from probcup.sources.statsbomb import build_aggregates


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
    """A confirmed pull beats a stale global benching — the core of the fix.
    Injects the stale override so the test is self-contained (not coupled to
    whatever the live overrides file happens to hold today)."""
    _, sb = build_aggregates()
    stale = {"goal_share": 0.3, "expected_minutes": 20}  # a stale global benching
    m = PlayerModel(sb)
    m._overrides = {**m._overrides, "Folarin Balogun": stale}
    assert m.rates("Folarin Balogun").expected_minutes == 20
    pulled = PlayerModel(sb, match_minutes={"Folarin Balogun": 90})
    pulled._overrides = {**pulled._overrides, "Folarin Balogun": stale}
    assert pulled.rates("Folarin Balogun").expected_minutes == 90  # the pull wins


def test_default_minutes_backward_compatible():
    """No override and no pull -> 90, unchanged behaviour."""
    _, sb = build_aggregates()
    assert PlayerModel(sb).rates("Nonexistent Player XYZ").expected_minutes == 90
