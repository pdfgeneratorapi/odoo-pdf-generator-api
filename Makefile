# Route the Odoo container by the branch we're on so the pre-commit hook
# tests against the matching major version. Override with `ODOO_SERVICE=...`
# on the command line to force a specific service.
GIT_BRANCH := $(shell git rev-parse --abbrev-ref HEAD 2>/dev/null)
ifeq ($(GIT_BRANCH),18.0)
    DEFAULT_ODOO_SERVICE := odoo18
else ifeq ($(GIT_BRANCH),17.0)
    DEFAULT_ODOO_SERVICE := odoo17
else
    DEFAULT_ODOO_SERVICE := odoo
endif
ODOO_SERVICE ?= $(DEFAULT_ODOO_SERVICE)
ODOO_DB ?= odoo
MODULE := pdfgeneratorapi_connector
BRIDGES := pdfgeneratorapi_connector_account pdfgeneratorapi_connector_sale pdfgeneratorapi_connector_purchase pdfgeneratorapi_connector_stock pdfgeneratorapi_connector_mrp
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

.PHONY: help setup lint lint-ruff lint-pylint lint-mypy format test test-unit test-odoo \
        coverage coverage-unit coverage-odoo coverage-clean \
        hooks upgrade demo-seed i18n-export i18n-translate i18n-check

# Default record count for `make demo-seed`. Override with `make demo-seed COUNT=10`.
COUNT ?= 1

help:
	@echo "setup            - install dev tooling via uv"
	@echo "hooks            - install the git pre-commit hook"
	@echo "lint             - run ruff + pylint-odoo + mypy"
	@echo "lint-mypy        - mypy type-check (Odoo-independent modules only)"
	@echo "format           - apply ruff formatting + import sorting"
	@echo "test             - run unit tests (host) + Odoo integration tests (container)"
	@echo "demo-seed        - ensure >= COUNT records of every supported doc type (default COUNT=1; idempotent)"
	@echo "coverage         - combined coverage (unit + Odoo), fail under threshold in pyproject"
	@echo "upgrade          - upgrade all addons (main + bridges) in the running Odoo container"
	@echo "i18n-export      - regenerate each addon's .pot from current source strings"
	@echo "i18n-translate   - rewrite every .po from the .pot + translations in scripts/i18n_translate.py"
	@echo "i18n-check       - msgfmt -cv every .po (fails on syntax errors / missing headers)"

setup:
	uv sync --group dev

hooks:
	uv run pre-commit install

lint: lint-ruff lint-pylint lint-mypy

lint-ruff:
	uv run ruff check .

lint-pylint:
	uv run pylint --rcfile=pyproject.toml -- $(shell find $(MODULE) $(BRIDGES) -type f -name "*.py" -not -path "*/__pycache__/*")

lint-mypy:
	uv run mypy

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

# Local-dev seeder: ensure ≥ COUNT records of every pdfgen-supported
# document type exist in the DB. Idempotent on COUNT — re-runs with the
# same value are no-ops; bumping COUNT adds the difference.
#
# First-time path: the `-i pdfgen_demo_data` installs the demo addon (a
# no-op once installed) and fires its post_init_hook which reads
# PDFGEN_DEMO_COUNT from the env. The subsequent odoo-shell call invokes
# `seed(env, count=$(COUNT))` directly so subsequent calls also honour
# the requested count without needing to re-install.
demo-seed:
	cd $(COMPOSE_DIR) && docker compose exec -T -e PDFGEN_DEMO_COUNT=$(COUNT) $(ODOO_SERVICE) bash -lc \
		"odoo -i pdfgen_demo_data -d $(ODOO_DB) --stop-after-init --no-http >/dev/null && \
		 echo 'from odoo.addons.pdfgen_demo_data.hooks import seed; seed(env, count=$(COUNT)); env.cr.commit()' | odoo shell -d $(ODOO_DB) --no-http"

# i18n — uses the running Odoo container to export .pot files (reads the
# installed module schema), then rewrites .po files from the translation dicts
# in scripts/i18n_translate.py. The rental bridge can't be exported via Odoo
# because it depends on sale_renting (Enterprise); its .pot is maintained by
# hand and the translator script is idempotent on it.
# Odoo <= 18 has no `odoo i18n` CLI (added in 19) — use the legacy
# --i18n-export flag against a loaded registry instead.
i18n-export:
	cd $(COMPOSE_DIR) && $(foreach m,$(MODULE) $(BRIDGES),\
		docker compose exec -T $(ODOO_SERVICE) odoo -d $(ODOO_DB) --no-http --stop-after-init \
			--i18n-export=/mnt/extra-addons/$(m)/i18n/$(m).pot --modules=$(m) 2>&1 | tail -1 ; )

i18n-translate:
	uv run python scripts/i18n_translate.py

i18n-check:
	@err=0; for f in $(MODULE)/i18n/*.po $(foreach b,$(BRIDGES),$(b)/i18n/*.po) ; do \
		[ -e "$$f" ] || continue ; \
		if ! msgfmt -cv "$$f" -o /dev/null ; then err=1 ; fi ; \
	done ; exit $$err
