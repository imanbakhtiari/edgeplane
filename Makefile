.PHONY: dev test lint frontend
PYTHON ?= .venv/bin/python
init:
	python3 tools/dev_env.py
dev:
	cd supervisor && docker compose up --build -d
test:
	cd agent && ../$(PYTHON) -m pytest -q
	cd supervisor && ../$(PYTHON) -m pytest -q
lint:
	.venv/bin/ruff check agent supervisor tools
frontend:
	npm --prefix supervisor/frontend run build
