"""Manual-entry policy transforms.

These functions convert independent model probabilities into manual-entry
recommendations for the manual SportsPredict workflow. The current pre-lock
workflow has no actionable field average, so normal calls pass no field value.
The field branch is retained only for historical/diagnostic replay.
"""

from __future__ import annotations

from dataclasses import dataclass

from questions.parser import MarketSpec


ODDS_GOAL_FAMILIES = {
    "btts",
    "btts_and_total_ge",
    "first_goal_and_other_scores_2h",
    "h2_more_goals_than_h1",
    "ht_team_winning",
    "ht_tied",
    "match_drawn",
    "team_advance",
    "team_clean_sheet",
    "team_first_goal_2h",
    "team_first_goal_game",
    "team_goals_ge",
    "team_more_goals_2h",
    "team_scores_both_halves",
    "team_scores_in_half",
    "team_win",
    "team_win_by_ge",
    "total_goals",
}

COUNT_FAMILIES = {
    "team_count_ge",
    "total_count_ge",
    "both_teams_count_ge",
    "team_more_count_than",
    "total_count_before_hydration",
}

SPECIAL_FAMILIES = {
    "goal_before_hydration",
    "goal_after_second_hydration",
    "goal_in_stoppage",
    "offside_before_hydration",
    "card_after_second_hydration",
    "substitution_before_halftime",
    "substitute_scores",
    "any_player_goals_ge",
    "any_player_sot_ge",
    "total_count_before_hydration",
}

UNRESOLVED_PLAYER_FLAGS = {
    "PRIOR_ONLY",
    "TEAM_UNKNOWN",
    "GLOBAL_MINUTES_UNCONFIRMED",
    "SCORER_PROP_RECHECK",
    "LINEUP_UNMATCHED_DEFAULT_90",
}

NO_FIELD_PLAYER_BASE_BLOCK_FLAGS = {
    "CONFIRMED_BENCH",
    "LINEUP_UNMATCHED_DEFAULT_90",
}

NO_FIELD_PLAYER_SOURCE_UNVALIDATED_FLAGS = {
    "PRIOR_ONLY",
    "TEAM_UNKNOWN",
}


@dataclass(frozen=True)
class ManualDecision:
    probability: int
    policy: str
    note: str
    field: int | None = None

    @property
    def display(self) -> str:
        return f"{self.probability}%"


def pct(value: float | int) -> int:
    """Round/clamp either 0-1 or 0-100 probability input to an integer percent."""
    p = float(value)
    if p <= 1:
        p *= 100
    return min(99, max(1, round(p)))


def _asym_below_field(model: int, field: int, *, cap: int) -> int:
    if model >= field:
        return field
    return min(99, max(1, field - min(cap, round(0.75 * (field - model)))))


def is_odds_goal_market(spec: MarketSpec) -> bool:
    return spec.family in ODDS_GOAL_FAMILIES


def is_count_market(spec: MarketSpec) -> bool:
    return spec.family in COUNT_FAMILIES


def is_special_market(spec: MarketSpec) -> bool:
    return spec.family in SPECIAL_FAMILIES or spec.stat == "shots"


def is_strong_sot_count(spec: MarketSpec) -> bool:
    return spec.stat == "sot" and spec.family in {"team_count_ge", "team_more_count_than"}


def unresolved_player_flags(flags: set[str] | list[str] | tuple[str, ...]) -> set[str]:
    return set(flags) & UNRESOLVED_PLAYER_FLAGS


def validated_player_no_field_base_applies(spec: MarketSpec) -> bool:
    """Whether the no-field global-base fallback is inside tested support.

    The chronological validation contains scorer rows, score/assist rows, and
    1+ SOT rows. It does not contain 2+/3+ SOT rows, so applying the global base
    there would be an unvalidated extrapolation.
    """

    if spec.family == "player_scores":
        return True
    if spec.family == "player_scores_or_assists":
        return True
    if spec.family == "player_sot_ge":
        return (spec.threshold or 1) <= 1
    return False


def manual_decision(
    spec: MarketSpec,
    model_probability: float | int,
    *,
    field_probability: float | int | None = None,
    flags: set[str] | list[str] | tuple[str, ...] = (),
    player_no_field_base: float | int | None = None,
) -> ManualDecision:
    """Apply the validated manual policy for one market.

    `model_probability` and `field_probability` may be 0-1 or 0-100. When no
    field is supplied, the model probability is returned with policy guidance.
    """

    model = pct(model_probability)
    field = pct(field_probability) if field_probability is not None else None
    flag_set = set(flags)
    unresolved = unresolved_player_flags(flag_set)
    source_unvalidated = bool(unresolved & NO_FIELD_PLAYER_SOURCE_UNVALIDATED_FLAGS)

    if spec.player and field is None and "CONFIRMED_BENCH" in flag_set:
        return ManualDecision(
            model,
            "player_no_field_confirmed_bench_model",
            "confirmed bench/no-field player prop; use the minutes-adjusted model and do not apply the global no-field base",
            None,
        )

    if spec.player and field is None and "LINEUP_UNMATCHED_DEFAULT_90" in flag_set:
        return ManualDecision(
            model,
            "research_required_no_field",
            "lineup was posted but the player could not be matched; verify manually before trusting the default-minutes model",
            None,
        )

    if (
        spec.player
        and field is None
        and source_unvalidated
        and "CURRENT_PROP_MARKET_ANCHOR" not in flag_set
        and not (flag_set & NO_FIELD_PLAYER_BASE_BLOCK_FLAGS)
        and validated_player_no_field_base_applies(spec)
    ):
        return ManualDecision(
            model,
                "research_required_no_field_source_unvalidated",
                (
                    "current-source proxy has no settled prior-only/team-unknown holdout rows; "
                    "use the raw model only as a placeholder until exact prop odds "
                    "or structured player research clears the source flag"
                ),
                None,
        )

    if (
        spec.player
        and field is None
        and player_no_field_base is not None
        and "CURRENT_PROP_MARKET_ANCHOR" not in flag_set
        and not (flag_set & NO_FIELD_PLAYER_BASE_BLOCK_FLAGS)
        and validated_player_no_field_base_applies(spec)
    ):
        fallback = model if spec.family == "player_scores" else pct(player_no_field_base)
        scorer_note = "scorer subfamily kept raw model; " if spec.family == "player_scores" else ""
        unresolved_note = " unresolved flags remain; recheck lineup/prop odds before lock." if unresolved else ""
        return ManualDecision(
            fallback,
            "player_no_field_raw_scores_else_global_base",
            (
                scorer_note
                + "no-field player-prop backtest favors raw scorer rows and global-base fallback for other player props."
                + unresolved_note
            ),
            None,
        )

    if (
        spec.player
        and field is None
        and player_no_field_base is not None
        and "CURRENT_PROP_MARKET_ANCHOR" not in flag_set
        and not (flag_set & NO_FIELD_PLAYER_BASE_BLOCK_FLAGS)
        and not validated_player_no_field_base_applies(spec)
    ):
        unresolved_note = "; unresolved flags remain, so prefer exact prop odds if available" if unresolved else ""
        source_note = (
            "; current-source proxy has no settled prior-only holdout rows"
            if source_unvalidated
            else ""
        )
        return ManualDecision(
            model,
            (
                "player_no_field_raw_model_unvalidated_threshold_and_source"
                if source_unvalidated
                else "player_no_field_raw_model_unvalidated_threshold"
            ),
            "no-field global-base fallback has no settled holdout support for this player-prop threshold; use raw model unless exact prop odds are available"
            + source_note
            + unresolved_note,
            None,
        )

    if spec.player and unresolved:
        if field is None:
            return ManualDecision(
                model,
                "research_required_no_field",
                "player prop has unresolved research flags; clear them before trusting this model value",
                None,
            )
        return ManualDecision(
            field,
            "unresolved_player_use_field",
            "unresolved player prop research flags; use field until researched",
            field,
        )

    if is_odds_goal_market(spec):
        return ManualDecision(
            model,
            "raw_odds_goal_model",
            "goal/outcome policy backtest favors raw odds-anchored model over field transforms",
            field,
        )

    if is_count_market(spec) and spec.stat == "cards":
        if field is None:
            return ManualDecision(
                model,
                "card_count_no_field_model",
                "field averages are unavailable before lock; use the independent card-count model and carry lower confidence",
                None,
            )
        return ManualDecision(field, "card_count_field", "card count backtest favors field", field)

    if is_count_market(spec) and is_strong_sot_count(spec):
        return ManualDecision(
            model,
            "strong_sot_count_model",
            "count backtest favors raw model for team/head-to-head SOT markets",
            field,
        )

    if spec.player:
        if field is None:
            return ManualDecision(
                model,
                "player_no_field_researched_model",
                "field averages are unavailable before lock; use the researched player model unless exact prop odds or lineups supersede it",
                None,
            )
        return ManualDecision(
            _asym_below_field(model, field, cap=10),
            "player_asym075_cap10",
            "player-prop backtest favors 10pt capped asymmetric field policy",
            field,
        )

    if is_special_market(spec):
        return ManualDecision(
            model,
            "raw_special_model",
            "StatsBomb special-market proxy replay favors raw special priors over generic field transforms",
            field,
        )

    if field is None:
        return ManualDecision(
            model,
            "global_no_field_model",
            "field averages are unavailable before lock; use the independent model and carry the family guardrail",
            None,
        )
    return ManualDecision(
        _asym_below_field(model, field, cap=12),
        "global_asym075_cap12",
        "global backtest favors 12pt capped asymmetric field policy",
        field,
    )
