---
name: player-prop-prior-fallback
description: "Player props silently fall back to a generic 0.12 prior when the player NAME fails to resolve; a [prior] flag on a star = a resolution bug, not a research gap"
metadata: 
  node_type: memory
  type: project
  originSessionId: edc0be82-f7ce-4493-be8b-e55f21f51524
---

Discovered 2026-06-28 (GER/PAR R32): player-prop pricing has a SILENT failure mode. When `spec.player` matches no StatsBomb history AND no override key, `PlayerModel.rates()` falls back to the generic prior (goal_share 0.12 / involvement 0.20 / SOT 0.72) and `p_scores` etc. price off that. The ONLY visible signal is the `source` tag printed in `daily_run`/`price_match` output: `[prior]` (or "prior only: research this player").

**Why:** the fallback is meant for genuinely-uncovered players (e.g. Matheus Cunha — Brazil not in the StatsBomb tournaments). But it ALSO silently swallows NAME-CAPTURE bugs: if the parser puts the wrong string in `spec.player`, a player who DOES have data resolves to None and gets the prior. A `[prior]` flag on a player who should obviously have data (Havertz, Musiala, Wirtz — all in StatsBomb via Euro 2024) is a BUG, not a research gap.

**The bug found:** some fixtures phrase props as "Will Kai Havertz **(Germany)** score…". The parser captured `spec.player = "Kai Havertz (Germany)"`, which matched nothing → every GER/PAR player prop fell to 0.12 prior (Havertz anytime 0.16 instead of ~0.48; Enciso's 0.889 SOT override bypassed). Fix: strip a trailing `"(…)"` from player in `MarketSpec.__post_init__` (parser.py) + regression test in test_parser.py. Was never submitted (0 prior submissions; caught at the night-run chart).

**Second instance + structural fix (2026-06-30, ESP/AUT + POR/CRO pre-fill):** the SAME class via a different mechanism — Iberian/Portuguese **full legal names**. StatsBomb stores "Cristiano Ronaldo dos Santos Aveiro" / "Bernardo Mota Veiga de Carvalho e Silva" / "Lamine Yamal Nasraoui Ebana", but questions use the common name ("Cristiano Ronaldo"). `_normalize_name` token-SORTS but matched on the EXACT token-set, so a 2-token common name never matched a 5-token full name → Ronaldo/Yamal/Bernardo Silva/Oyarzabal/Bruno Fernandes all floored to 0.12 (Ronaldo anytime 0.12 vs real 0.41). FIX (structural, not per-player): `PlayerModel.resolve()` now has a unique-token-SUBSET fallback — if the query's tokens (>=2) are a subset of exactly ONE history full name, resolve to it (uniqueness guard avoids "Silva"/"Bernardo" ambiguity). This auto-resolves the whole full-name class; no overrides needed. Snapshot prior count 9→4 after.

**The team-as-player twin (same day):** "Will **the United States** have 6+ shots on target?" → the ambiguous SOT rule checked `m["name"] in teams` without stripping the leading article, so a TEAM-SOT count market parsed to player_sot_ge (team as player → prior → stored 1% vs correct team_count_ge 37%, a near-catastrophic Brier). Fix = strip "the " before the membership check (parser.py, matches the player_scores idiom). A `[prior]` flag whose subject is a TEAM name is this twin.

**How to apply:** when scanning the daily output, treat a `[prior]`/`sparse` flag on a player from a StatsBomb-covered team as a RED FLAG — run `PlayerModel.resolve(spec.player)` vs `resolve("Plain Name")` to check it's a name-capture/parser bug before assuming you just need a club-form override. Distinguish: resolve(plain)=hit but resolve(spec.player)=None → parser/name bug; both None → genuinely uncovered, needs an override. The full-legal-name and leading-article subclasses are now fixed structurally (resolve() subset fallback + parser article-strip), so a REMAINING [prior] on a covered-team star is most likely the parenthetical-capture bug or a true gap. Same silent-degradation theme as the lineup staleness class. See [[lineup-puller]], [[parser-ko-normalization]], [[override-levers]], [[goal-method-models]].
