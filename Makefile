PY ?= .venv/bin/python
# Run without any inherited PYTHONPATH (e.g. a ROS install) leaking in.
export PYTHONPATH :=

.PHONY: setup test demo goldens figures paper data backtest

setup:            ## create .venv with the exact tournament dependencies
	python3 -m venv .venv
	$(PY) -m pip install -q -r requirements.lock
	$(PY) -m pip install -q -e . --no-deps

test:             ## unit tests + golden equivalence tests (offline)
	$(PY) -m pytest

demo:             ## price a real tournament board offline, no API keys
	$(PY) -m probcup demo

goldens:          ## verbose check of the current code against the tournament-final goldens
	$(PY) tests/golden/make_goldens.py check

figures:          ## rebuild results/figures from results/data
	$(PY) -m pip install -q "matplotlib>=3.8"
	$(PY) results/make_figures.py

paper:            ## rebuild paper/report.pdf: figures and numbers from results/data, then LaTeX
	$(PY) -m pip install -q "matplotlib>=3.8"
	$(PY) paper/build_assets.py
	cd paper && latexmk -pdf -interaction=nonstopmode -halt-on-error report.tex

data:             ## download raw StatsBomb + football-data.co.uk data (~950 MB)
	$(PY) -m probcup download-data

backtest:         ## walk-forward club backtest (needs `make data`)
	$(PY) -m probcup backtest
