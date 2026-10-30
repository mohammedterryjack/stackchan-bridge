# Stack-chan MCP bridge. Quick start: make setup
SHELL := /bin/bash
CONTAINER ?= stackchan-bridge
export BRIDGE_CONTAINER := $(CONTAINER)
COMPOSE := docker compose
PLACEHOLDER := your-xiaozhi-mcp-endpoint-goes-here

.PHONY: help check env build up setup run logs test stop restart

help:
	@echo "make setup    check tools, check .env, build and start the bridge"
	@echo "make run      same as setup"
	@echo "make logs     follow bridge logs"
	@echo "make test     call one tool on every server inside the running bridge"
	@echo "make stop     stop the bridge"
	@echo "make restart  restart the bridge"

check:
	@command -v docker >/dev/null || { echo "ERROR: docker not found. Install Docker Desktop or Docker Engine." >&2; exit 1; }
	@docker compose version >/dev/null 2>&1 || { echo "ERROR: 'docker compose' not available. Install the Docker Compose v2 plugin." >&2; exit 1; }
	@docker info >/dev/null 2>&1 || { echo "ERROR: Docker daemon not running. Start Docker and retry." >&2; exit 1; }

env:
	@if [ ! -f .env ]; then cp .env.example .env; echo "ERROR: created .env from .env.example. Edit .env, set MCP_ENDPOINT to your Xiaozhi MCP endpoint (wss://...), then run make setup again." >&2; exit 1; fi
	@grep -q '^MCP_ENDPOINT=wss\?://' .env || { echo "ERROR: .env has no MCP_ENDPOINT=wss://... line. Set it and retry." >&2; exit 1; }
	@! grep -q '$(PLACEHOLDER)' .env || { echo "ERROR: .env still has the example MCP_ENDPOINT. Replace it with your own endpoint." >&2; exit 1; }

build: check env
	$(COMPOSE) build stackchan-bridge

up: check env
	$(COMPOSE) up -d --build --no-deps stackchan-bridge

setup run: up
	@echo "Bridge started as $(CONTAINER). Check with: make logs  /  make test"

logs:
	docker logs -f --tail 100 $(CONTAINER)

test:
	@fail=0; while IFS='|' read -r srv tool args; do [ -z "$$srv" ] || [ "$${srv:0:1}" = "#" ] && continue; out=$$(docker exec -i $(CONTAINER) python - "$$srv" "$$tool" "$$args" < tools/sc_t.py 2>&1); last=$$(tail -1 <<<"$$out"); if grep -q 'err=False' <<<"$$last"; then echo "PASS $$srv/$$tool $$last"; else echo "FAIL $$srv/$$tool $$last"; fail=1; fi; done < tools/smoke.txt; exit $$fail

stop:
	$(COMPOSE) stop stackchan-bridge

restart:
	$(COMPOSE) restart stackchan-bridge
