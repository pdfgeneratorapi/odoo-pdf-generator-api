"""One-time migration: copy global ICP pdfgen credentials onto each
res.company so single-tenant installs keep working unchanged after the
per-company fields land.

Runs once on upgrade to 19.0.2.0.0. Idempotent — if a company already has
its own value for a field, we leave it alone.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

_KEYS = (
    "api_base_url",
    "api_key",
    "api_secret",
    "workspace_identifier",
    "editor_web_url",
)


def migrate(cr, version):
    # Reuse the upgrade's cursor. Opening a second cursor via `registry.cursor()`
    # deadlocks against locks the outer migration holds (e.g. ir_module_module).
    env = api.Environment(cr, SUPERUSER_ID, {})
    icp = env["ir.config_parameter"].sudo()
    values = {k: icp.get_param(f"pdfgen.{k}") for k in _KEYS}
    if not any(values.values()):
        _logger.info("pdfgen post-migrate: no global ICP values to copy.")
        return
    companies = env["res.company"].search([])
    for company in companies:
        updates = {}
        for key, value in values.items():
            if not value:
                continue
            field = f"pdfgen_{key}"
            if not getattr(company, field):
                updates[field] = value
        if updates:
            company.write(updates)
            _logger.info(
                "pdfgen post-migrate: copied %d values onto company %s.",
                len(updates),
                company.name,
            )
