"""No-miss backlog of unpriced market types.

The guarantee: every market the platform offers is checked, every question the
parser cannot yet handle is recorded, and nothing is ever silently dropped — so
"I forgot to read that / I overlooked a question" cannot happen.

It scans the FULL market set (every match in the snapshot, not just the submit
horizon), parses each question, and maintains:
  - data/logs/unparsed_backlog.json  persistent, deduplicated, per-question:
      {type, count, first_seen, last_seen, status}  (status: open|resolved)
  - data/logs/unparsed_backlog.md    human-readable, grouped by type, priority-sorted

Anything that doesn't match a KNOWN type lands in the `other` bucket (still
recorded, flagged for review) — that's the catch-all so a brand-new template is
never missed. When a previously-open question starts parsing (a model shipped),
it is auto-marked `resolved`, so the file also tracks progress.

    python scripts/scan_backlog.py            # full audit, prints summary
    python scripts/scan_backlog.py --quiet    # update files only
daily_run also calls scan_and_update() every pass over the full market set.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from questions.parser import parse, UnparsedQuestion  # noqa: E402
from questions.teams import ALL_TEAM_NAMES  # noqa: E402

BACKLOG_JSON = ROOT / "data" / "logs" / "unparsed_backlog.json"
BACKLOG_MD = ROOT / "data" / "logs" / "unparsed_backlog.md"

# Known unpriced families -> one model clears a whole type. `other` is the
# catch-all so an unseen template is still captured, never dropped.
def classify(question: str) -> str:
    s = question.lower()
    # Order matters: specific families first. The stoppage WINDOW ("half stoppage"
    # / "stoppage (added) time" / "in stoppage time") must NOT be confused with the
    # regulation qualifier "(90 minutes + stoppage time)" that suffixes many KO markets.
    if "hydration break" in s:
        return "timing-hydration"
    if "half stoppage" in s or "stoppage (added) time" in s or "in stoppage time" in s:
        return "timing-stoppage"
    if "clean sheet" in s:
        return "clean-sheet"
    if "advance" in s or "reach the round" in s or "qualify" in s:
        return "advancement"
    if "any player" in s:
        return "squad-aggregate"
    if "substitute" in s and "score" in s:
        return "substitute-scores"
    if "substitution" in s or "substitute" in s:
        return "substitution-timing"
    if "shots (on and off" in s or ("total shots" in s and "target" not in s):
        return "total-shots"
    return "other"   # unknown template — recorded + flagged, never silently dropped


def _parses(question: str) -> bool:
    try:
        parse(question, teams=ALL_TEAM_NAMES)
        return True
    except UnparsedQuestion:
        return False
    except Exception:
        # A parse rule that matched but errored is a real bug, not an unpriced
        # market — let it surface rather than hiding it in the backlog.
        return True


def scan_and_update(markets_df) -> dict:
    """Scan EVERY question in markets_df, refresh the persistent backlog, return
    a summary dict. Safe to call every run (idempotent)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    counts = markets_df["question"].value_counts().to_dict()
    unparseable = {q: classify(q) for q in counts if not _parses(q)}

    data = {}
    if BACKLOG_JSON.exists():
        try:
            data = json.loads(BACKLOG_JSON.read_text())
        except Exception:
            data = {}

    # Record/refresh everything currently unparseable.
    for q, typ in unparseable.items():
        e = data.get(q, {"first_seen": now, "status": "open"})
        e.update(type=typ, last_seen=now, count=counts.get(q, 1))
        e.setdefault("first_seen", now)
        e["status"] = "open"
        data[q] = e

    # Auto-resolve: an open backlog item that now parses (a model shipped).
    for q, e in data.items():
        if e.get("status") == "open" and q not in unparseable and _parses(q):
            e["status"] = "resolved"
            e["resolved_at"] = now

    BACKLOG_JSON.parent.mkdir(parents=True, exist_ok=True)
    BACKLOG_JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    _write_md(data, total_markets=len(markets_df), parseable=len(counts) - len(unparseable))
    open_items = {q: e for q, e in data.items() if e.get("status") == "open"}
    return {
        "total_markets": len(markets_df),
        "distinct_questions": len(counts),
        "unparseable_distinct": len(unparseable),
        "unparseable_markets": sum(counts.get(q, 0) for q in unparseable),
        "open_backlog": len(open_items),
        "by_type": {t: sum(e["count"] for q, e in open_items.items() if e["type"] == t)
                    for t in sorted({e["type"] for e in open_items.values()})},
    }


def _write_md(data: dict, total_markets: int, parseable: int) -> None:
    now = datetime.now(timezone.utc)
    open_items = {q: e for q, e in data.items() if e.get("status") == "open"}
    by_type: dict[str, list] = defaultdict(list)
    for q, e in open_items.items():
        by_type[e["type"]].append((q, e))
    n_mkts = sum(e["count"] for e in open_items.values())
    lines = [
        "# Unparsed-question backlog (auto-maintained by scripts/scan_backlog.py)",
        "",
        f"Updated {now:%Y-%m-%d %H:%M UTC}. **{len(open_items)} open template(s) across "
        f"{n_mkts} market instances.** (Full scan of {total_markets} markets; "
        f"{parseable} distinct questions parse.)",
        "",
        "Each `type` = one model to build (priority = total markets, KO are 2x weight). "
        "`other` = a template that matched no known type — review immediately.",
    ]
    for typ in sorted(by_type, key=lambda t: -sum(e["count"] for _, e in by_type[t])):
        items = by_type[typ]
        n = sum(e["count"] for _, e in items)
        flag = "  ⚠️ UNKNOWN TYPE — REVIEW" if typ == "other" else ""
        lines.append(f"\n## {typ} — {len(items)} template(s), {n} markets{flag}")
        for q, e in sorted(items, key=lambda x: -x[1]["count"]):
            lines.append(f"- [{e['count']}x · since {e['first_seen'][:10]}] {q}")
    resolved = [q for q, e in data.items() if e.get("status") == "resolved"]
    if resolved:
        lines.append(f"\n## resolved ({len(resolved)}) — now parse, model shipped")
        for q in sorted(resolved):
            lines.append(f"- {q}")
    BACKLOG_MD.write_text("\n".join(lines) + "\n")


def main():
    quiet = "--quiet" in sys.argv
    from probability_cup import ProbabilityCup
    mk = ProbabilityCup(bot=1).markets_df()        # FULL set, every match
    summary = scan_and_update(mk)
    if not quiet:
        print(f"scanned {summary['total_markets']} markets "
              f"({summary['distinct_questions']} distinct questions)")
        print(f"PARSE: {summary['distinct_questions'] - summary['unparseable_distinct']}"
              f"/{summary['distinct_questions']} distinct parse; "
              f"{summary['unparseable_markets']} market instances unpriced "
              f"({summary['open_backlog']} open templates)")
        for t, n in sorted(summary["by_type"].items(), key=lambda x: -x[1]):
            print(f"  {n:>3} markets  {t}")
        print(f"-> {BACKLOG_MD.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
