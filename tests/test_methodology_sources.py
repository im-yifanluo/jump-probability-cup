from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "methodology_sources.md"


def test_methodology_sources_cover_primary_guardrails():
    text = DOC.read_text(encoding="utf-8")

    for required in [
        "Gneiting and Raftery",
        "Dixon and Coles",
        "Wolfers and Zitzewitz",
        "Goto, Takeishi, and Yairi",
        "Clegg, Song, and Cartlidge",
        "Cawley and Talbot",
        "StatsBomb open data",
        "Football-Data",
    ]:
        assert required in text

    for rule in [
        "Do not extremize",
        "predeclared",
        "chronological holdout",
        "bootstrap",
        "Odds source quality",
        "series_slug=soccer-fifwc",
        "New odds-conversion papers are research leads",
        "must not read `/predictions`",
        "must not submit",
    ]:
        assert rule in text


def test_manual_and_strategy_docs_link_methodology_guardrails():
    for path in [
        ROOT / "docs" / "manual_strategy.md",
        ROOT / "docs" / "strategy_research_2026-06-27.md",
        ROOT / "docs" / "strategies.md",
        ROOT / "docs" / "full_research_program.md",
    ]:
        assert "docs/methodology_sources.md" in path.read_text(encoding="utf-8")
