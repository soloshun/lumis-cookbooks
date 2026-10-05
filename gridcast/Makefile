# GridCast — convenience targets. Everything delegates to `gridcastctl` (uv run).
SHELL := /bin/bash
CTL   := uv run gridcastctl
SCENARIO ?= A

.PHONY: help env install up down purge status verify urls test lint check images chaos-list chaos revert reveal logs

help:  ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n",$$1,$$2}'

env:  ## Create .env from .env.example (if missing)
	@test -f .env || cp .env.example .env && echo ".env ready"

install:  ## Install the Python toolchain (uv) environment
	uv sync --all-extras

up: env install  ## Create cluster + platform, build images, load data, train models, deploy
	$(CTL) up

down:  ## Delete the cluster and stop the platform (keeps data volumes)
	$(CTL) down

purge:  ## Delete everything including volumes and local GitOps/chaos state
	$(CTL) down --purge

status:  ## Pods, platform, plan freshness
	$(CTL) status

verify:  ## End-to-end checks across services, data and telemetry
	$(CTL) verify

urls:  ## Print local URLs
	$(CTL) urls

images:  ## Rebuild and push all service images
	$(CTL) images build

test:  ## Unit tests
	uv run pytest

lint:  ## Ruff + mypy
	uv run ruff check src tests
	uv run mypy src

check: lint test  ## Lint + unit tests

chaos-list:  ## List incident scenarios
	$(CTL) chaos list

chaos:  ## Inject a scenario: make chaos SCENARIO=A
	$(CTL) chaos inject $(SCENARIO)

revert:  ## Revert the active scenario
	$(CTL) chaos revert

reveal:  ## Show the latest run's ground truth
	$(CTL) chaos reveal
