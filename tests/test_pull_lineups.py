"""Regression guard for the 2026-06-26 stale-benched failure mode.

Covers the ESPN confirmed-lineup puller (scripts/pull_lineups.py) and the
expected_minutes precedence in PlayerModel. Deterministic — no network."""
import sys
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import pull_lineups as PL
from models.players import PlayerModel
from wcdata.statsbomb import build_aggregates


def _roster(*players):
    """(displayName, starter) pairs -> an ESPN-shaped team roster block."""
    return {"roster": [{"starter": st, "athlete": {"displayName": n}} for n, st in players]}


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None


def _board_event():
    return {
        "events": [
            {
                "id": "401",
                "name": "United States vs Bosnia and Herzegovina",
                "competitions": [
                    {
                        "competitors": [
                            {"team": {"displayName": "United States"}},
                            {"team": {"displayName": "Bosnia and Herzegovina"}},
                        ]
                    }
                ],
            }
        ]
    }


def _posted_summary():
    return {
        "rosters": [
            _roster(
                ("Folarin Balogun", True),
                *[(f"US Starter {i}", True) for i in range(10)],
            ),
            _roster(*[(f"BIH Starter {i}", True) for i in range(11)]),
        ]
    }


class _FlakySummarySession:
    def __init__(self, *, summary_failures: int):
        self.summary_failures = summary_failures
        self.summary_calls = 0

    def get(self, url, *, params, timeout):
        assert timeout == PL.REQUEST_TIMEOUT_SECONDS
        if url == PL.SCOREBOARD:
            return _Response(_board_event())
        if url == PL.SUMMARY:
            self.summary_calls += 1
            if self.summary_calls <= self.summary_failures:
                raise PL.requests.exceptions.ReadTimeout("slow ESPN summary")
            return _Response(_posted_summary())
        raise AssertionError(f"unexpected url: {url}")


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


def test_pull_lineup_retries_transient_espn_summary_timeout(monkeypatch):
    monkeypatch.setattr(PL.time, "sleep", lambda _seconds: None)
    session = _FlakySummarySession(summary_failures=1)

    result = PL.pull_lineup(
        "USA vs BIH",
        datetime(2026, 6, 30, tzinfo=timezone.utc),
        players={"Folarin Balogun"},
        session=session,
    )

    assert session.summary_calls == 2
    assert result["status"] == "pulled"
    assert result["minutes"] == {"Folarin Balogun": 90}


def test_pull_lineup_returns_source_error_after_retry_exhaustion(monkeypatch):
    monkeypatch.setattr(PL.time, "sleep", lambda _seconds: None)
    session = _FlakySummarySession(summary_failures=PL.REQUEST_ATTEMPTS + 1)

    result = PL.pull_lineup(
        "USA vs BIH",
        datetime(2026, 6, 30, tzinfo=timezone.utc),
        players={"Folarin Balogun"},
        session=session,
    )

    assert session.summary_calls == PL.REQUEST_ATTEMPTS
    assert result["status"] == "source_error"
    assert result["minutes"] == {}
    assert result["unmatched"] == ["Folarin Balogun"]
    assert "failed after" in result["error"]
