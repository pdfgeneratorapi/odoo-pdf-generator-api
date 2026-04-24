ODOO_SERVICE ?= odoo
ODOO_DB ?= odoo
MODULE := pdfgeneratorapi_connector
BRIDGES := pdfgeneratorapi_connector_sale pdfgeneratorapi_connector_purchase pdfgeneratorapi_connector_stock pdfgeneratorapi_connector_mrp
comma := ,
empty :=
space := $(empty) $(empty)
# Comma-joined lists: Odoo's -u / --test-tags / coverage --source all want commas,
# but BRIDGES is space-separated so callers can add entries without remembering
# separators. $(subst ...) strips the spaces make's foreach inserts between
# iterations — without that we'd get `,sale ,purchase` instead of `,sale,purchase`.
MODULES := $(subst $(space),,$(MODULE)$(foreach b,$(BRIDGES),$(comma)$(b)))
TEST_TAGS := $(subst $(space),,/$(MODULE)$(foreach b,$(BRIDGES),$(comma)/$(b)))
COVERAGE_SOURCES := $(subst $(space),,/mnt/extra-addons/$(MODULE)$(foreach b,$(BRIDGES),$(comma)/mnt/extra-addons/$(b)))
COMPOSE_DIR := /Users/brunofarias/code/ar/odoo
REPO_ROOT := $(CURDIR)
# .coverage file written by odoo into the bind-mounted main addon dir
# (visible at this host path). Bridges aren't directly writable by the
# Odoo container's coverage run but their code is covered via --source.
ODOO_COVERAGE_FILE_HOST := $(REPO_ROOT)/$(MODULE)/.coverage.odoo

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
	uv run pylint --rcfile=pyproject.toml -- $(shell find $(MODULE) $(BRIDGES) -type f -name "*.py" -not -path "*/__pycache__/*")

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
		   --source=$(COVERAGE_SOURCES) \
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
