"""One-time initialization + system exploration.

Finds the Probability Cup event, joins its lobby, and snapshots all matches
and open markets to data/ so you can study the question types offline.

Run:  python3 scripts/initialize.py
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from sp_api import Client

DATA = Path(__file__).resolve().parent.parent / "data"
DATA.mkdir(exist_ok=True)


def main():
    c = Client()

    # 1. Find the Probability Cup
    event = c.probability_cup_event()
    print(f"Event: {event['title']}  ({event['start_date']} -> {event['end_date']})")
    print(f"  id: {event['id']}, status: {event['status']}")

    # 2. Find + join the shared lobby
    lobby = c.lobbies(event["id"])[0]
    if lobby.get("joined"):
        print(f"Lobby: {lobby['name']} ({lobby['id']}) — already joined")
    else:
        c.join_lobby(lobby["id"])
        print(f"Lobby: {lobby['name']} ({lobby['id']}) — joined now")

    # 3. Snapshot matches
    matches = c.matches(event_id=event["id"])
    matches.sort(key=lambda m: m.get("opening_time") or "")
    print(f"\nMatches with open markets: {len(matches)}")
    now = datetime.now(timezone.utc).isoformat()
    for m in matches[:10]:
        print(f"  {m['opening_time']}  {m['name']}  ({m['open_market_count']} open markets)")
    if len(matches) > 10:
        print(f"  ... and {len(matches) - 10} more")

    # 4. Snapshot all open markets, fetched per match (gentle on rate limit:
    #    ~72 calls at 60/min is fine with the client's backoff)
    all_markets = []
    for i, m in enumerate(matches):
        mkts = c.markets(lobby_id=lobby["id"], match_id=m["id"])
        all_markets.extend(mkts)
        if (i + 1) % 20 == 0:
            print(f"  fetched markets for {i + 1}/{len(matches)} matches...")

    print(f"Open markets total: {len(all_markets)}")

    # 5. Show the distinct question templates so we know what we must model
    templates = {}
    for mkt in all_markets:
        q = mkt["question"]
        for m in matches:
            for token in m["name"].split(" vs "):
                q = q.replace(token, "{team}")
        templates[q] = templates.get(q, 0) + 1
    print("\nQuestion templates (normalized):")
    for q, n in sorted(templates.items(), key=lambda kv: -kv[1]):
        print(f"  [{n:3d}x] {q}")

    # 6. Persist snapshot
    snapshot = {
        "fetched_at": now,
        "event": event,
        "lobby": lobby,
        "matches": matches,
        "markets": all_markets,
    }
    out = DATA / "snapshot.json"
    out.write_text(json.dumps(snapshot, indent=2))
    print(f"\nSnapshot written to {out}")

    # 7. Existing predictions, if any
    preds = c.predictions(lobby_id=lobby["id"])
    print(f"Your existing predictions: {len(preds)}")


if __name__ == "__main__":
    main()
