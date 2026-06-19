.PHONY: help install dev test test-quick lint build-plugin cloud-dev clean

help:
	@echo "magi-control-plane developer targets:"
	@echo "  make install        editable install + dev deps"
	@echo "  make test           full pytest"
	@echo "  make test-quick     fast subset (no network)"
	@echo "  make lint           ruff"
	@echo "  make build-plugin   compile policies → plugin/managed-settings.json"
	@echo "  make cloud-dev      start cloud API (uvicorn)"
	@echo "  make clean          drop caches"

install:
	python3 -m pip install -e ".[dev]"

test:
	pytest -q

test-quick:
	pytest -q -m "not network"

lint:
	ruff check src tests

build-plugin:
	python3 -m magi_cp.policy.compiler policies/legal_filing_v1.json plugin/managed-settings.json

cloud-dev:
	uvicorn magi_cp.cloud.app:app --reload --port 8787

clean:
	rm -rf .pytest_cache **/__pycache__ build dist *.egg-info
