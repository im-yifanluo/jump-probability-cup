# SportsPredict Model API: notes from live use

The official reference is the [Model API documentation](https://sportspredict.com/probability/api).
These are the behaviours that mattered in practice, several of which differ from the docs.
The client is [`sportspredict/client.py`](../src/probcup/sportspredict/client.py); everything
else goes through the domain layer, [`sportspredict/cup.py`](../src/probcup/sportspredict/cup.py).

```
Base https://api.sportspredict.com/api/v1     Authorization: Bearer sp_live_...
GET  /events -> GET /lobbies?event_id= -> POST /lobbies/{id}/join -> GET /matches?event_id=
GET  /markets?lobby_id=&match_id=      POST /predictions/batch (<= 50)   PATCH /predictions/{id}
GET  /predictions?lobby_id=            GET  /results?lobby_id=
```

- **`opening_time` is the kickoff, which is the prediction deadline.** `closing_time` is about
  2.5 hours later. The docs describe these the other way round.
- **One key per leaderboard entry.** Each bot key has its own prediction pool. Writing with one
  key does nothing for the other; verify each bot separately.
- **Reads return percentages.** `GET /predictions` returns `probability: 40`, not `0.40`, despite
  the docs. Writes take integers 1–99. The domain layer converts both ways.
- **Upsert by hand.** A second POST for the same market returns 409; revisions are PATCHes to
  the prediction id. `ProbabilityCup.submit()` plans create / update / unchanged / skip and
  never writes past kickoff.
- **Names differ by endpoint.** Match names use FIFA codes ("URU vs ESP"), questions use full
  names ("Uruguay"), external feeds use their own spellings ("Côte d'Ivoire", "Korea Republic").
  [`questions/teams.py`](../src/probcup/questions/teams.py) bridges them.
- **`/events` finds the event by title.** The documented `type == "probability"` is a UUID in practice.
- **Open items only.** `/matches` and `/markets` list only matches with open markets; settled
  markets drop out, so the snapshot cache is the record of what was asked.
- **Rate limit: 60 requests per minute.** A full snapshot is one call per match; the client backs
  off on 429 and retries 5xx.
- **No crowd data during the tournament.** `/results` returned only our own Brier per market, so
  the crowd's numbers were copied by hand from the website ([crowd_history.md](archive/crowd_history.md)).
  After the event, `/results` also returns `crowd_value` (the field's average probability) and the
  stage-weighted `rbp` per market, which is how [results/data](../results/data/) was exported.
