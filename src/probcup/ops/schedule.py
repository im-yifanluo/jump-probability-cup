"""Compute the dynamic final-pass schedule for the day's remaining kickoff waves.

The morning run calls this, then creates a ONE-SHOT cron at each printed fire time
(~LEAD minutes before each wave — late enough that official lineups are out, early
enough to research + reprice without rushing). The latest wave is tagged `night`
(prices tomorrow + outputs tomorrow's predictions); earlier waves are `final` (the
imminent wave only).

All times are computed in the SYSTEM LOCAL timezone — the same zone the cron tool
uses — so the printed cron expressions need no further conversion. A 03:00Z (10pm CT)
kickoff therefore correctly maps to a 9:10pm-CT fire on the LOCAL calendar date.

    probcup schedule                    # default 50-min lead, 18h window
    probcup schedule --lead-minutes 45
"""
import argparse
from datetime import datetime, timedelta

import pandas as pd

from probcup.sportspredict.cup import ProbabilityCup


def main(argv: list[str] | None = None):
    ap = argparse.ArgumentParser(prog="probcup schedule", description=__doc__)
    ap.add_argument("--lead-minutes", type=int, default=50,
                    help="fire this many minutes before kickoff (default 50: after the "
                         "~60-75min lineup release, with buffer to reprice unhurried)")
    ap.add_argument("--within-hours", type=float, default=18,
                    help="only schedule waves kicking off within this horizon (keeps it "
                         "to today's remaining slate, not tomorrow's afternoon)")
    ap.add_argument("--min-buffer-minutes", type=int, default=8,
                    help="skip a wave whose fire time is already nearer than this")
    args = ap.parse_args(argv)

    pc = ProbabilityCup(bot=1)
    mk = pc.markets_df(open_only=True, max_age=timedelta(hours=1))
    now = pd.Timestamp.now(tz="UTC")
    horizon = now + pd.Timedelta(hours=args.within_hours)
    up = mk[(mk["kickoff"] > now) & (mk["kickoff"] <= horizon)]
    waves = sorted(up["kickoff"].drop_duplicates())
    if not waves:
        print(f"NO-WAVES: no upcoming kickoffs within {args.within_hours:g}h — "
              "nothing to schedule.")
        return

    now_local = datetime.now().astimezone()
    print(f"# now {now_local:%a %Y-%m-%d %I:%M%p %Z}; lead={args.lead_minutes}min; "
          f"{len(waves)} wave(s) within {args.within_hours:g}h\n")
    last = waves[-1]
    for ko in waves:
        ko = pd.Timestamp(ko)
        fire = (ko.to_pydatetime() - timedelta(minutes=args.lead_minutes)).astimezone()
        ko_local = ko.to_pydatetime().astimezone()
        kind = "night" if ko == last else "final"
        hours = 26 if kind == "night" else 5
        n = up[up["kickoff"] == ko]["match_name"].nunique()
        mins_away = (fire - now_local).total_seconds() / 60
        if mins_away < args.min_buffer_minutes:
            print(f"# SKIP {ko_local:%a %I:%M%p} kickoff — fire is only "
                  f"{mins_away:.0f}min away (too late for a fresh pass)")
            continue
        cron = f"{fire.minute} {fire.hour} {fire.day} {fire.month} *"
        print(f'SCHEDULE | kind={kind} | hours={hours} | cron="{cron}" | '
              f'fire={fire:%a %I:%M%p} | kickoff={ko_local:%a %I:%M%p} ({n} match)')

    print("\n# AGENT: first CronList + CronDelete any existing [AUTO-DAY] one-shots, then for")
    print("# each SCHEDULE line create a one-shot cron (recurring:false) at its cron= expr.")
    print("#   kind=final -> [AUTO-DAY] read matchday.md and run probcup run --hours 5 (both bots)")
    print("#   kind=night -> [AUTO-DAY] read night_run.md, probcup run --hours 26, OUTPUT tomorrow")


if __name__ == "__main__":
    main()
