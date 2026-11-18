VENV = api/.venv/bin
export DATABASE_URL ?= postgresql://offpeak:offpeak@localhost:5432/offpeak

.PHONY: up db setup test lint sample proof plans

up:
	docker compose up --build

db:
	docker compose up -d --wait db

setup:
	python3 -m venv api/.venv
	$(VENV)/pip install -q -r api/requirements-dev.txt
	cd web && npm ci

test: db
	cd api && .venv/bin/python -m pytest -q
	cd web && npx vitest run

lint:
	$(VENV)/ruff check api scripts --exclude api/.venv
	cd web && npx tsc --noEmit

sample:
	mkdir -p samples
	$(VENV)/python scripts/gen_nem12.py samples/clean.csv
	$(VENV)/python scripts/gen_nem12.py samples/defects.csv --defects

plans:
	cd api && .venv/bin/python plans.py

proof: db
	$(VENV)/python scripts/proof.py
