import sys
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from referee_card_adjustment_audit import CardMatch, _method_table, load_matches, replay  # noqa: E402


def test_load_matches_reads_referee_and_card_columns(tmp_path):
    csv_path = tmp_path / "2526_E0.csv"
    csv_path.write_text(
        "\n".join(
            [
                "Div,Date,HomeTeam,AwayTeam,Referee,HY,AY,HR,AR",
                "E0,07/08/25,Alpha,Beta,Ref One,2,3,0,1",
                "E0,08/08/25,Gamma,Delta,,1,1,0,0",
                "E0,09/08/25,Eta,Theta,Ref Two,1,2,0,0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    rows = load_matches(tmp_path)

    assert [row.referee for row in rows] == ["Ref One", "Ref Two"]
    assert rows[0].total_cards == 6
    assert rows[1].total_cards == 3


def test_referee_shrinkage_beats_league_prior_when_referee_signal_is_stable():
    start = date(2026, 1, 1)
    matches = []
    for idx in range(80):
        strict = idx % 2 == 0
        matches.append(
            CardMatch(
                key=f"m{idx}",
                match_date=start + timedelta(days=idx),
                div="T1",
                season_file="fixture.csv",
                home=f"H{idx}",
                away=f"A{idx}",
                referee="Strict Ref" if strict else "Lenient Ref",
                total_cards=6 if strict else 2,
            )
        )

    scored = replay(matches, min_global_train=20, min_ref_matches=2)
    rows = {row["method"]: row for row in _method_table(scored)}

    assert float(rows["referee_shrink20"]["delta_vs_league_prior_x100"]) > 0
    assert float(rows["referee_shrink20"]["brier"]) < float(rows["league_prior"]["brier"])
