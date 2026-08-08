"""Special-market pricing for knockout-round Probability Cup questions.

These are markets that are not standard scoreline/count/player props:
hydration-break timing, substitute goals, any-player props, and knockout
advance.  The parameters come from downloaded StatsBomb international
tournament events when available, with conservative constants only as fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from math import exp, factorial, log
from pathlib import Path
from typing import Iterable

import numpy as np
from scipy import stats


ROOT = Path(__file__).resolve().parent.parent.parent


def _shot_from_outside_penalty_area(event: dict) -> bool:
    loc = event.get("location") or []
    if len(loc) < 2:
        return True
    x, y = float(loc[0]), float(loc[1])
    return not (x >= 102 and 18 <= y <= 62)


def _moment_alpha(values: list[int]) -> float:
    arr = np.asarray(values, dtype=float)
    mu = float(arr.mean()) if len(arr) else 0.0
    if mu <= 0:
        return 0.0
    var = float(arr.var(ddof=1)) if len(arr) > 1 else mu
    return max(0.0, (var - mu) / (mu * mu))


def _nb_ge(mean: float, alpha: float, threshold: int) -> float:
    if threshold <= 0:
        return 1.0
    if mean <= 0:
        return 0.0
    if alpha < 1e-6:
        return float(stats.poisson.sf(threshold - 1, mean))
    size = 1.0 / alpha
    p = size / (size + mean)
    return float(stats.nbinom.sf(threshold - 1, size, p))


def _poisson_grid(lam: float, n: int = 10) -> np.ndarray:
    vals = np.array([exp(-lam) * lam**i / factorial(i) for i in range(n + 1)], dtype=float)
    return vals / vals.sum()


@dataclass(frozen=True)
class SpecialBaselines:
    n_matches: int
    rates: dict[str, float]
    means: dict[str, float]
    shots_per_sot: float
    total_shots_alpha: float
    early_corner_share: float
    early_corners_alpha: float
    extra_time_goal_factor: float
    extra_time_card_factor: float
    source: str

    @classmethod
    def fallback(cls) -> "SpecialBaselines":
        # Measured from the local StatsBomb cache on 2026-06-27. Used only if
        # the raw event files are absent in a fresh checkout.
        return cls(
            n_matches=314,
            rates={
                "goal_before_30": 0.4395,
                "goal_after_30_1h": 0.3567,
                "goal_after_75": 0.4522,
                "goal_1h_stoppage": 0.0701,
                "goal_2h_stoppage": 0.2070,
                "offside_before_30": 0.6274,
                "card_after_75": 0.6561,
                "card_stoppage": 0.3631,
                "first_card_before_first_goal": 0.5350,
                "substitution_before_halftime": 0.1783,
                "substitute_goal": 0.2707,
                "own_goal": 0.1242,
                "outside_box_goal": 0.2420,
                "header_goal": 0.3312,
                "any_player_goal": 0.8885,
                "any_player_brace": 0.1624,
                "any_player_2_sot": 0.7070,
            },
            means={
                "goals": 2.455,
                "shots": 23.398,
                "sot": 7.401,
                "offsides": 3.115,
                "cards": 3.462,
                "corners": 8.793,
                "corners_before_30": 2.596,
                "substitutions": 7.400,
            },
            shots_per_sot=3.161,
            total_shots_alpha=0.022,
            early_corner_share=0.295,
            early_corners_alpha=0.048,
            extra_time_goal_factor=0.758,
            extra_time_card_factor=1.434,
            source="fallback constants from local StatsBomb cache",
        )


def _statsbomb_event_dirs() -> list[Path]:
    roots = [
        ROOT / "data" / "raw" / "statsbomb",
        ROOT / "data_reference" / "raw" / "statsbomb",
    ]
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        out.extend(path for path in sorted(root.glob("*/events")) if path.exists())
    return out


def _event_files() -> Iterable[Path]:
    for events_dir in _statsbomb_event_dirs():
        yield from sorted(events_dir.glob("*.json"))


@lru_cache(maxsize=1)
def load_special_baselines() -> SpecialBaselines:
    files = list(_event_files())
    if not files:
        return SpecialBaselines.fallback()

    match_rows: list[dict] = []
    et_match_count = 0
    et_goals = 0
    et_cards = 0

    for path in files:
        events = json.loads(path.read_text())
        starters: set[str] = set()
        subs_in: set[str] = set()
        substitutions: list[float] = []
        goals: list[tuple[float, int, str | None, bool]] = []
        own_goals: list[float] = []
        outside_box_goals: list[float] = []
        header_goals: list[float] = []
        shots: list[float] = []
        sot: list[float] = []
        offsides: list[float] = []
        cards: list[float] = []
        stoppage_cards: list[float] = []
        corners: list[float] = []
        player_goals: dict[str, int] = {}
        player_sot: dict[str, int] = {}
        saw_extra_time = False

        for event in events:
            typ = event.get("type", {}).get("name")
            if typ == "Starting XI":
                for slot in event.get("tactics", {}).get("lineup", []):
                    starters.add(slot["player"]["name"])
            elif typ == "Substitution":
                replacement = event.get("substitution", {}).get("replacement", {}).get("name")
                if replacement:
                    subs_in.add(replacement)
                substitutions.append(event.get("minute", 0) + event.get("second", 0) / 60)

        for event in events:
            typ = event.get("type", {}).get("name")
            period = event.get("period")
            minute = event.get("minute", 0) + event.get("second", 0) / 60
            player = event.get("player", {}).get("name")
            if period in (3, 4):
                saw_extra_time = True

            if typ == "Shot":
                outcome = event.get("shot", {}).get("outcome", {}).get("name")
                if period in (1, 2):
                    shots.append(minute)
                    if outcome in {"Goal", "Saved", "Saved To Post"}:
                        sot.append(minute)
                        if player:
                            player_sot[player] = player_sot.get(player, 0) + 1
                    if outcome == "Goal":
                        is_sub = bool(player and player in subs_in and player not in starters)
                        goals.append((minute, period, player, is_sub))
                        if _shot_from_outside_penalty_area(event):
                            outside_box_goals.append(minute)
                        if event.get("shot", {}).get("body_part", {}).get("name") == "Head":
                            header_goals.append(minute)
                        if player:
                            player_goals[player] = player_goals.get(player, 0) + 1
                elif period in (3, 4) and outcome == "Goal":
                    et_goals += 1
            elif typ == "Own Goal For":
                if period in (1, 2):
                    goals.append((minute, period, None, False))
                    own_goals.append(minute)
                elif period in (3, 4):
                    et_goals += 1
            elif typ == "Pass":
                pass_data = event.get("pass", {})
                if period in (1, 2) and pass_data.get("type", {}).get("name") == "Corner":
                    corners.append(minute)
                if period in (1, 2) and pass_data.get("outcome", {}).get("name") == "Pass Offside":
                    offsides.append(minute)
            elif typ == "Offside" and period in (1, 2):
                offsides.append(minute)
            elif typ == "Foul Committed":
                card = event.get("foul_committed", {}).get("card", {}).get("name")
                if card and period in (1, 2):
                    cards.append(minute)
                    if (period == 1 and minute >= 45) or (period == 2 and minute >= 90):
                        stoppage_cards.append(minute)
                elif card and period in (3, 4):
                    et_cards += 1
            elif typ == "Bad Behaviour":
                card = event.get("bad_behaviour", {}).get("card", {}).get("name")
                if card and period in (1, 2):
                    cards.append(minute)
                    if (period == 1 and minute >= 45) or (period == 2 and minute >= 90):
                        stoppage_cards.append(minute)
                elif card and period in (3, 4):
                    et_cards += 1

        if saw_extra_time:
            et_match_count += 1

        match_rows.append(
            {
                "goals": len(goals),
                "shots": len(shots),
                "sot": len(sot),
                "offsides": len(offsides),
                "cards": len(cards),
                "corners": len(corners),
                "corners_before_30": sum(1 for minute in corners if minute < 30),
                "substitutions": len(substitutions),
                "goal_before_30": any(period == 1 and minute < 30 for minute, period, _, _ in goals),
                "goal_after_30_1h": any(period == 1 and minute >= 30 for minute, period, _, _ in goals),
                "goal_after_75": any(period == 2 and minute >= 75 for minute, period, _, _ in goals),
                "goal_1h_stoppage": any(period == 1 and minute >= 45 for minute, period, _, _ in goals),
                "goal_2h_stoppage": any(period == 2 and minute >= 90 for minute, period, _, _ in goals),
                "offside_before_30": any(minute < 30 for minute in offsides),
                "card_after_75": any(minute >= 75 for minute in cards),
                "card_stoppage": bool(stoppage_cards),
                "first_card_before_first_goal": bool(cards) and (
                    not goals or min(cards) < min(minute for minute, _, _, _ in goals)
                ),
                "substitution_before_halftime": any(minute < 45 for minute in substitutions),
                "substitute_goal": any(is_sub for _, _, _, is_sub in goals),
                "own_goal": bool(own_goals),
                "outside_box_goal": bool(outside_box_goals),
                "header_goal": bool(header_goals),
                "any_player_goal": bool(player_goals),
                "any_player_brace": any(count >= 2 for count in player_goals.values()),
                "any_player_2_sot": any(count >= 2 for count in player_sot.values()),
            }
        )

    n = len(match_rows)
    if n == 0:
        return SpecialBaselines.fallback()

    def mean(name: str) -> float:
        return float(sum(row[name] for row in match_rows) / n)

    rates = {
        name: mean(name)
        for name in (
            "goal_before_30",
            "goal_after_30_1h",
            "goal_after_75",
            "goal_1h_stoppage",
            "goal_2h_stoppage",
            "offside_before_30",
            "card_after_75",
            "card_stoppage",
            "first_card_before_first_goal",
            "substitution_before_halftime",
            "substitute_goal",
            "own_goal",
            "outside_box_goal",
            "header_goal",
            "any_player_goal",
            "any_player_brace",
            "any_player_2_sot",
        )
    }
    means = {name: mean(name) for name in ("goals", "shots", "sot", "offsides", "cards", "corners", "corners_before_30", "substitutions")}
    reg_goal_rate = means["goals"] / 90
    reg_card_rate = means["cards"] / 90
    et_goal_rate = et_goals / max(et_match_count * 30, 1)
    et_card_rate = et_cards / max(et_match_count * 30, 1)

    return SpecialBaselines(
        n_matches=n,
        rates=rates,
        means=means,
        shots_per_sot=means["shots"] / max(means["sot"], 1e-9),
        total_shots_alpha=_moment_alpha([row["shots"] for row in match_rows]),
        early_corner_share=means["corners_before_30"] / max(means["corners"], 1e-9),
        early_corners_alpha=_moment_alpha([row["corners_before_30"] for row in match_rows]),
        extra_time_goal_factor=et_goal_rate / max(reg_goal_rate, 1e-9),
        extra_time_card_factor=et_card_rate / max(reg_card_rate, 1e-9),
        source="local StatsBomb raw event cache",
    )


@dataclass
class SpecialPricer:
    goals: object
    counts: object
    baselines: SpecialBaselines | None = None

    def __post_init__(self):
        if self.baselines is None:
            self.baselines = load_special_baselines()

    def _total_goal_mean(self) -> float:
        return float(self.goals.lam_home + self.goals.lam_away)

    def _count_mean(self, stat: str) -> float:
        return float(self.counts.mean(stat, "home") + self.counts.mean(stat, "away"))

    def _scaled_event(self, rate_name: str, mean_name: str, model_mean: float) -> float:
        assert self.baselines is not None
        base = self.baselines.rates[rate_name]
        historical_mean = self.baselines.means[mean_name]
        if base <= 0 or historical_mean <= 0 or model_mean <= 0:
            return 0.0
        if base >= 1:
            return 1.0
        per_unit_hazard = -log(1 - base) / historical_mean
        return float(1 - exp(-per_unit_hazard * model_mean))

    def p_goal_before_hydration(self) -> float:
        return self._scaled_event("goal_before_30", "goals", self._total_goal_mean())

    def p_goal_after_first_hydration_1h(self) -> float:
        return self._scaled_event("goal_after_30_1h", "goals", self._total_goal_mean())

    def p_goal_after_second_hydration(self) -> float:
        return self._scaled_event("goal_after_75", "goals", self._total_goal_mean())

    def p_goal_in_stoppage(self, period: str) -> float:
        rate = "goal_1h_stoppage" if period == "1h" else "goal_2h_stoppage"
        return self._scaled_event(rate, "goals", self._total_goal_mean())

    def p_offside_before_hydration(self) -> float:
        return self._scaled_event("offside_before_30", "offsides", self._count_mean("offsides"))

    def p_card_after_second_hydration(self, *, include_extra_time: bool) -> float:
        assert self.baselines is not None
        p_reg = self._scaled_event("card_after_75", "cards", self._count_mean("cards"))
        if not include_extra_time:
            return p_reg
        et_mean = self._count_mean("cards") * (30 / 90) * self.baselines.extra_time_card_factor
        p_extra_time_card = 1 - exp(-max(et_mean, 0.0))
        return float(p_reg + (1 - p_reg) * self.goals.p_draw() * p_extra_time_card)

    def p_card_in_stoppage_any_half(self) -> float:
        return self._scaled_event("card_stoppage", "cards", self._count_mean("cards"))

    def p_first_card_before_first_goal(self) -> float:
        card_mean = max(self._count_mean("cards"), 0.0)
        goal_mean = max(self._total_goal_mean(), 0.0)
        total = card_mean + goal_mean
        if total <= 0:
            return 0.0
        return float(card_mean / total * (1 - exp(-total)))

    def p_substitution_before_halftime(self) -> float:
        assert self.baselines is not None
        return self.baselines.rates["substitution_before_halftime"]

    def p_substitute_scores(self) -> float:
        return self._scaled_event("substitute_goal", "goals", self._total_goal_mean())

    def p_own_goal(self) -> float:
        return self._scaled_event("own_goal", "goals", self._total_goal_mean())

    def p_outside_box_goal(self) -> float:
        return self._scaled_event("outside_box_goal", "goals", self._total_goal_mean())

    def p_header_goal(self) -> float:
        return self._scaled_event("header_goal", "goals", self._total_goal_mean())

    def p_any_player_goals_ge(self, threshold: int) -> float:
        if threshold == 1:
            return self._scaled_event("any_player_goal", "goals", self._total_goal_mean())
        if threshold != 2:
            raise ValueError(f"unsupported any-player goals threshold: {threshold}")
        return self._scaled_event("any_player_brace", "goals", self._total_goal_mean())

    def p_any_player_sot_ge(self, threshold: int) -> float:
        if threshold != 2:
            raise ValueError(f"unsupported any-player SOT threshold: {threshold}")
        return self._scaled_event("any_player_2_sot", "sot", self._count_mean("sot"))

    def p_total_shots_ge(self, threshold: int) -> float:
        assert self.baselines is not None
        mean = self._count_mean("sot") * self.baselines.shots_per_sot
        return _nb_ge(mean, self.baselines.total_shots_alpha, threshold)

    def p_corners_before_hydration_ge(self, threshold: int) -> float:
        assert self.baselines is not None
        mean = self._count_mean("corners") * self.baselines.early_corner_share
        return _nb_ge(mean, self.baselines.early_corners_alpha, threshold)

    def p_total_substitutions_ge(self, threshold: int) -> float:
        assert self.baselines is not None
        return _nb_ge(self.baselines.means["substitutions"], 0.0, threshold)

    def p_advance(self, side: str) -> float:
        """Probability a team advances from a knockout tie.

        Uses regulation win directly from the scoreline model. Conditional on a
        regulation draw, extra time is modeled as a 30-minute Poisson race using
        historical ET/regulation scoring intensity; if ET is also drawn, penalty
        shootout probability is close to 50 with only a small strength tilt.
        """
        assert self.baselines is not None
        p_reg_win = self.goals.p_team_win(side)
        p_reg_draw = self.goals.p_draw()
        factor = max(0.25, min(1.75, self.baselines.extra_time_goal_factor))
        lam_home_et = self.goals.lam_home * (30 / 90) * factor
        lam_away_et = self.goals.lam_away * (30 / 90) * factor
        home = _poisson_grid(lam_home_et)
        away = _poisson_grid(lam_away_et)
        if side == "home":
            p_et_win = sum(home[i] * away[:i].sum() for i in range(1, len(home)))
            strength = (self.goals.lam_home - self.goals.lam_away) / max(self._total_goal_mean(), 1e-9)
        else:
            p_et_win = sum(away[i] * home[:i].sum() for i in range(1, len(away)))
            strength = (self.goals.lam_away - self.goals.lam_home) / max(self._total_goal_mean(), 1e-9)
        p_et_draw = sum(home[i] * away[i] for i in range(len(home)))
        p_pens = min(0.58, max(0.42, 0.5 + 0.08 * strength))
        return float(p_reg_win + p_reg_draw * (p_et_win + p_et_draw * p_pens))

    def p_penalty_shootout(self) -> float:
        """Probability the knockout match reaches a penalty shootout."""
        assert self.baselines is not None
        p_reg_draw = self.goals.p_draw()
        factor = max(0.25, min(1.75, self.baselines.extra_time_goal_factor))
        lam_home_et = self.goals.lam_home * (30 / 90) * factor
        lam_away_et = self.goals.lam_away * (30 / 90) * factor
        home = _poisson_grid(lam_home_et)
        away = _poisson_grid(lam_away_et)
        p_et_draw = sum(home[i] * away[i] for i in range(len(home)))
        return float(p_reg_draw * p_et_draw)
