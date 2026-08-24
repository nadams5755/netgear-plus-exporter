VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

.PHONY: venv lint test clean

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

clean:
	rm -rf $(VENV) .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
