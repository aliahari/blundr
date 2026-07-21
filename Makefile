# Makefile for Blundr project
# Uses uv for Python package management

.PHONY: help install dev run test lint format clean

# Python version
PYTHON_VERSION := 3.11

help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# Backend targets

install: ## Install Python dependencies with uv
	uv sync

venv: ## Create virtual environment with uv
	uv venv

backend: install ## Start backend server
	uv run python run.py

run-backend: backend ## Alias for backend

# Development targets

dev: ## Start both backend and frontend
	./run_dev.sh

# Frontend targets

frontend: ## Start frontend server
	cd frontend && npm run dev

frontend-install: ## Install frontend dependencies
	cd frontend && npm install

frontend-build: ## Build frontend for production
	cd frontend && npm run build

# Testing targets

test: ## Run backend tests
	uv run pytest

test-watch: ## Run tests in watch mode
	uv run ptw

test-cov: ## Run tests with coverage
	uv run pytest --cov=app --cov-report=term

# Linting and formatting

lint: ## Run linting with ruff
	uv run ruff check app tests

lint-fix: ## Auto-fix linting issues
	uv run ruff check --fix app tests

format: ## Format code with ruff
	uv run ruff format app tests

# Cleanup targets

clean: ## Remove build artifacts
	rm -rf .venv
	rm -rf __pycache__
	rm -rf *.egg-info
	rm -rf .pytest_cache
	rm -rf .coverage
	rm -rf htmlcov/

clean-all: clean ## Remove all build artifacts including frontend
	rm -rf frontend/node_modules
	rm -rf frontend/dist
	rm -rf frontend/build

# Docker targets

docker-build: ## Build Docker image
	docker build -t blundr-backend .

docker-run: ## Run Docker container
	docker run -p 8000:8000 blundr-backend

docker-dev: ## Run Docker container with volume mounting
	docker run -p 8000:8000 -v $(PWD):/app blundr-backend

# Setup targets

setup: install frontend-install ## Setup entire project
	@echo "Installing backend dependencies..."
	uv sync
	@echo "Installing frontend dependencies..."
	cd frontend && npm install
	@echo "Setup complete!"

setup-dev: venv install ## Setup with virtual environment
	uv venv
	uv sync
