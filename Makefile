ODOO_SERVICE ?= odoo
ODOO_DB ?= odoo
MODULE := pdfgeneratorapi_connector
BRIDGES := pdfgeneratorapi_connector_sale
# Comma-joined list for Odoo's -u / --test-tags.
MODULES := $(MODULE),$(BRIDGES)
TEST_TAGS := /$(MODULE),$(foreach b,$(BRIDGES),/$(b))
COMPOSE_DIR := /Users/brunofarias/code/ar/odoo
REPO_ROOT := $(CURDIR)
# .coverage file written by odoo into the bind-mounted main addon dir
# (visible at this host path). Bridges aren't directly writable by the
# Odoo container's coverage run but their code is covered via --source.
ODOO_COVERAGE_FILE_HOST := $(REPO_ROOT)/$(MODULE)/.coverage.odoo
# Comma-joined --source list: main addon + every bridge.
COVERAGE_SOURCES := /mnt/extra-addons/$(MODULE)$(foreach b,$(BRIDGES),$(comma)/mnt/extra-addons/$(b))
comma := ,

.PHONY: help setup lint lint-ruff lint-pylint format test test-unit test-odoo \
        coverage coverage-unit coverage-odoo coverage-clean \
        hooks upgrade

help:
	@echo "setup            - install dev tooling via uv"
	@echo "hooks            - install the git pre-commit hook"
	@echo "lint             - run ruff + pylint-odoo"
	@echo "format           - apply ruff formatting + import sorting"
	@echo "test             - run unit tests (host) + Odoo integration tests (container)"
	@echo "coverage         - combined coverage (unit + Odoo), fail under threshold in pyproject"
	@echo "upgrade          - upgrade all addons (main + bridges) in the running Odoo container"

setup:
	uv sync --group dev

hooks:
	uv run pre-commit install

lint: lint-ruff lint-pylint

lint-ruff:
	uv run ruff check .

lint-pylint:
	uv run pylint --rcfile=pyproject.toml -- $(shell find pdfgeneratorapi_connector pdfgeneratorapi_connector_sale -type f -name "*.py" -not -path "*/__pycache__/*")

format:
	uv run ruff format .
	uv run ruff check --select I --fix .

test: test-unit test-odoo

test-unit:
	uv run pytest tests_unit -v

test-odoo:
	cd $(COMPOSE_DIR) && docker compose exec -T $(ODOO_SERVICE) odoo \
		-d $(ODOO_DB) \
		-u $(MODULES) \
		--test-enable \
		--test-tags $(TEST_TAGS) \
		--stop-after-init \
		--no-http \
		--http-port=18069 \
		--gevent-port=18072

coverage-clean:
	rm -f .coverage .coverage.unit .coverage.odoo .coverage.* 2>/dev/null || true
	rm -f $(ODOO_COVERAGE_FILE_HOST) 2>/dev/null || true

coverage-unit: coverage-clean
	uv run coverage run --data-file=.coverage.unit -m pytest tests_unit -q

coverage-odoo:
	cd $(COMPOSE_DIR) && docker compose exec -T $(ODOO_SERVICE) bash -c \
		"pip install --quiet --user --break-system-packages 'coverage[toml]>=7' >/dev/null && \
		 cd /mnt/extra-addons/$(MODULE) && \
		 /var/lib/odoo/.local/bin/coverage run \
		   --data-file=.coverage.odoo \
		   --source=/mnt/extra-addons/$(MODULE),/mnt/extra-addons/$(BRIDGES) \
		   --branch \
		   /usr/bin/odoo \
		     -d $(ODOO_DB) \
		     -u $(MODULES) \
		     --test-enable \
		     --test-tags $(TEST_TAGS) \
		     --stop-after-init \
		     --no-http \
		     --http-port=18069 \
		     --gevent-port=18072"
	mv $(ODOO_COVERAGE_FILE_HOST) $(REPO_ROOT)/.coverage.odoo

coverage: coverage-unit coverage-odoo
	uv run coverage combine --keep .coverage.unit.* .coverage.odoo
	uv run coverage report

upgrade:
	cd $(COMPOSE_DIR) && docker compose exec -T $(ODOO_SERVICE) odoo \
		-d $(ODOO_DB) \
		-u $(MODULES) \
		--stop-after-init \
		--no-http
	cd $(COMPOSE_DIR) && docker compose restart $(ODOO_SERVICE)
