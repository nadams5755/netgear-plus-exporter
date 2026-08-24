VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: venv lint test test-integration clean

venv: $(VENV)/bin/activate

$(VENV)/bin/activate: pyproject.toml
	python3 -m venv $(VENV)
	$(PIP) install -e ".[dev]"
	touch $(VENV)/bin/activate

lint: venv
	$(VENV)/bin/ruff check .

test: venv
	$(VENV)/bin/pytest
	$(VENV)/bin/ruff check .

# Opt-in: needs a live netgear-plus-exporter process and real switches. See
# tests/integration/test_live_switches.py for the NETGEAR_PLUS_EXPORTER_URL /
# NETGEAR_PLUS_INTEGRATION_TARGETS env vars. Forced serial (-n0): concurrent
# probes of the same target would race the exporter's single-flight lock.
test-integration: venv
	$(VENV)/bin/pytest -m integration -n0

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
