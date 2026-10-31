"""Team-name resolution for the 2026 World Cup event.

SportsPredict match names use FIFA codes or short names ("KOR vs CZE",
"GER vs Curacao") while market questions use full names ("South Korea",
"Czechia"). This module maps between them and identifies each match's two
teams in question-name space.
"""

from __future__ import annotations

# FIFA code (or short name as it appears in match names) -> full name as it
# appears in question text. Covers all 48 teams in the snapshot.
CODE_TO_NAME = {
    "ALG": "Algeria",
    "ARG": "Argentina",
    "AUS": "Australia",
    "AUT": "Austria",
    "BEL": "Belgium",
    "BIH": "Bosnia and Herzegovina",
    "BRA": "Brazil",
    "CAN": "Canada",
    "CIV": "Ivory Coast",
    "COD": "DR Congo",
    "COL": "Colombia",
    "CPV": "Cape Verde",
    "CRO": "Croatia",
    "CZE": "Czechia",
    "Curacao": "Curaçao",
    "ECU": "Ecuador",
    "EGY": "Egypt",
    "ENG": "England",
    "ESP": "Spain",
    "FRA": "France",
    "GER": "Germany",
    "GHA": "Ghana",
    "Haiti": "Haiti",
    "IRN": "Iran",
    "IRQ": "Iraq",
    "JOR": "Jordan",
    "JPN": "Japan",
    "KOR": "South Korea",
    "KSA": "Saudi Arabia",
    "MAR": "Morocco",
    "MEX": "Mexico",
    "NED": "Netherlands",
    "NOR": "Norway",
    "New Zealand": "New Zealand",
    "PAN": "Panama",
    "PAR": "Paraguay",
    "POR": "Portugal",
    "QAT": "Qatar",
    "RSA": "South Africa",
    "SCO": "Scotland",
    "SEN": "Senegal",
    "SUI": "Switzerland",
    "SWE": "Sweden",
    "TUN": "Tunisia",
    "TUR": "Türkiye",
    "URU": "Uruguay",
    "USA": "United States",
    "UZB": "Uzbekistan",
}

ALL_TEAM_NAMES = frozenset(CODE_TO_NAME.values())

# External feeds (ESPN, odds sites) use different conventions for some teams.
# Keys are our canonical names; values are normalized alias forms (lowercase,
# ascii, alphanumeric+spaces). Extend here whenever a feed mismatch appears.
EXTERNAL_ALIASES = {
    "Türkiye": {"turkiye", "turkey"},
    "Ivory Coast": {"ivory coast", "cote divoire", "cote d ivoire", "cote d'ivoire"},
    "Cape Verde": {"cape verde", "cabo verde", "cape verde islands"},
    "DR Congo": {"dr congo", "congo dr", "democratic republic of the congo", "dr of congo"},
    "South Korea": {"south korea", "korea republic", "republic of korea"},
    "United States": {"united states", "usa", "united states of america"},
    "Bosnia and Herzegovina": {"bosnia and herzegovina", "bosnia herzegovina", "bosnia"},
    "Czechia": {"czechia", "czech republic"},
    "Iran": {"iran", "ir iran", "iran islamic republic"},
    "Curaçao": {"curacao"},
    "Saudi Arabia": {"saudi arabia", "ksa"},
    "New Zealand": {"new zealand"},
}


def _normalize(name: str) -> str:
    import re
    import unicodedata
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", folded.lower()).strip()


def names_match(our_name: str, external_name: str) -> bool:
    """Does an external feed's team name refer to our canonical team?"""
    ext = _normalize(external_name)
    if ext in EXTERNAL_ALIASES.get(our_name, set()):
        return True
    ours = _normalize(our_name)
    return ours == ext or ours in ext or ext in ours


def match_teams(match_name: str) -> tuple[str, str]:
    """'KOR vs CZE' -> ('South Korea', 'Czechia'). First listed team first."""
    left, _, right = match_name.partition(" vs ")
    return CODE_TO_NAME[left.strip()], CODE_TO_NAME[right.strip()]
