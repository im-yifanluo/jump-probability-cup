# Rejected: Monte Carlo game-state simulator

**Idea.** The analytic pricer prices each market from a static snapshot of team strength. A
favourite that goes ahead sits back and cedes shots and corners to the chasing side; a
minute-by-minute simulation captures that, and gives every market from one set of correlated
outcomes ([simulator.py](simulator.py), [sim_pricer.py](sim_pricer.py)).

**Measurement.** Anchored to the same odds and count means, the simulator reproduces the
analytic marginals within Monte Carlo noise ([test_simulator.py](test_simulator.py)) and
changed backtested prices by about 0.0003 Brier: game state moves events *within* a match,
not the totals and comparisons the markets ask about.

**Decision.** Not worth the runtime or the extra moving part. Kept out of production
(June 14 and again June 20).

    pytest research/rejected/monte_carlo_simulator
