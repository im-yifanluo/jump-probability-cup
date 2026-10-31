"""probcup: the forecasting system behind two top-1% entries in the Jump Trading
Probability Cup (2026 FIFA World Cup).

Subpackages:
    sportspredict  platform API client and the domain layer (ProbabilityCup)
    questions      question text -> structured market spec
    sources        odds, lineups, player rates, historical datasets
    models         goal grid, count models, player props, the pricer
    pipeline       pricer assembly, bot-2 experiments, the production run
    ops            pre-kickoff scheduling and the missed-run watchdog
    evaluation     club backtest, crowd ledger, A/B replay, drift monitor
"""

__version__ = "1.0.0"
