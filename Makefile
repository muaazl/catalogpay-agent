# CatalogPay Agent: common tasks.  `make help` lists them.
# On Windows without make, use scripts/dev.ps1 (or the commands below directly).

PY ?= $(if $(wildcard backend/.venv/Scripts/python.exe),backend/.venv/Scripts/python.exe,backend/.venv/bin/python)

.PHONY: help install dev backend frontend test test-backend test-frontend lint build samples docker-build docker-run

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-15s %s\n", $$1, $$2}'

install: ## Create the venv and install backend + frontend dependencies
	test -d backend/.venv || python3 -m venv backend/.venv
	$(PY) -m pip install -e "backend[dev]"
	cd frontend && npm ci

dev: ## Run API (:8000) and Vite dev server (:5173) together
	./scripts/dev.sh

backend: ## Run only the API with auto-reload
	cd backend && $(abspath $(PY)) -m uvicorn app.main:app --reload --port 8000

frontend: ## Run only the Vite dev server
	cd frontend && npm run dev

test: test-backend test-frontend ## Run every test suite

test-backend: ## pytest
	cd backend && $(abspath $(PY)) -m pytest -q

test-frontend: ## vitest
	cd frontend && npm run test -- --run

lint: ## ruff + TypeScript type-check
	cd backend && $(abspath $(PY)) -m ruff check . && $(abspath $(PY)) -m ruff format --check .
	cd frontend && npm run typecheck

build: ## Production frontend build (served by FastAPI from frontend/dist)
	cd frontend && npm run build

samples: ## Regenerate samples/ (needs: pip install -e "backend[samples]")
	$(PY) scripts/generate_samples.py

docker-build: ## Build the production image
	docker build -t catalogpay-agent .

# No --env-file: the image already defaults DATABASE_URL to the /data volume, and the example
# file's local sqlite path would override it. Pass real keys with -e GEMINI_API_KEY=... etc.
docker-run: ## Run the image on http://localhost:8000 with a persistent volume
	docker run --rm -p 8000:8000 -v catalogpay-data:/data catalogpay-agent
