.DEFAULT_GOAL := help

.PHONY: help install dev test lint synth reset-demo

help:
	@echo "Selaras Backend"
	@echo "  make install     Install dependencies"
	@echo "  make dev         Run development server"
	@echo "  make test        Run all tests with coverage"
	@echo "  make lint        Run ruff linter"
	@echo "  make synth       Generate synthetic data (seed=42, n=300 fast)"
	@echo "  make synth-full  Generate full 2000 companies"
	@echo "  make reset-demo  Reset demo data via admin API"

install:
	pip install -e ".[dev]"

dev:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest --cov=app --cov-report=term-missing -v

lint:
	ruff check app/ synth/ eval/ tests/

synth:
	python -m synth.generator --companies 300 --seed 42

synth-full:
	python -m synth.generator --companies 2000 --seed 42

reset-demo:
	curl -X POST http://localhost:8000/admin/synth/reset \
	  -H "X-API-Key: $$(grep ADMIN_API_KEY .env | cut -d= -f2)"

dashboard:
	streamlit run dashboard/app.py

audit:
	pip-audit
