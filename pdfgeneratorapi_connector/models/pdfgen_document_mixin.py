"""Abstract mixin that any document model can inherit to expose the
`Generate custom PDF` button + wizard flow. Bridge modules (e.g.
pdfgeneratorapi_connector_sale) just `_inherit` the mixin on the target
model and add a view to surface the button.

Also hosts the shared pdfgen config-read helper used by every wizard —
per-company value if set, else global `ir.config_parameter` fallback.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .pdfgen_api_client import DEFAULT_BASE_URL, PdfGenApiClient


def pdfgen_config(env, key):
    """Return the effective pdfgen config value for the current company.

    Resolution order:
      1. `res.company.pdfgen_<key>` on `env.company` if set (per-company
         override, added in Phase "multi-company").
      2. `ir.config_parameter` `pdfgen.<key>` — global fallback, the
         pre-multi-company behaviour.
      3. `None` when neither has a value.
    """
    company = env.company
    value = getattr(company, f"pdfgen_{key}", None) if company else None
    if value:
        return value
    return env["ir.config_parameter"].sudo().get_param(f"pdfgen.{key}") or None


def build_pdfgen_client(env):
    """Shared client factory used by every wizard — reads pdfgen_config
    for creds and raises a translatable UserError if anything's missing."""
    key = pdfgen_config(env, "api_key")
    secret = pdfgen_config(env, "api_secret")
    workspace = pdfgen_config(env, "workspace_identifier")
    if not (key and secret and workspace):
        raise UserError(
            env._("PDF Generator API is not configured. Go to Settings > PDF Generator API.")
        )
    return PdfGenApiClient(
        base_url=pdfgen_config(env, "api_base_url") or DEFAULT_BASE_URL,
        api_key=key,
        api_secret=secret,
        workspace_identifier=workspace,
        editor_web_url=pdfgen_config(env, "editor_web_url") or None,
    )


class PdfgenDocumentMixin(models.AbstractModel):
    _name = "pdfgen.document.mixin"
    _description = "Expose the PDF Generator wizard on a document model"

    pdfgen_configured = fields.Boolean(
        compute="_compute_pdfgen_configured",
        help="True when PDF Generator API credentials are present.",
    )

    @api.depends_context("uid", "allowed_company_ids")
    def _compute_pdfgen_configured(self):
        ready = bool(
            pdfgen_config(self.env, "api_key")
            and pdfgen_config(self.env, "api_secret")
            and pdfgen_config(self.env, "workspace_identifier")
        )
        for record in self:
            record.pdfgen_configured = ready

    def action_open_pdfgen_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Generate custom PDF"),
            "res_model": "pdfgen.generate.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_res_model": self._name,
                "default_res_id": self.id,
            },
        }

    def action_view_pdfgen_async_jobs_from_list(self):
        """Open the Async Jobs list filtered to the selected records.

        When invoked from a list-view header button the recordset is the
        current selection, so we scope the jobs view to those rows. With
        no selection (form-view button or programmatic call on an empty
        recordset) we fall back to all jobs for the model.
        """
        domain = [("res_model", "=", self._name)]
        if self.ids:
            domain.append(("res_id", "in", self.ids))
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Async PDF Jobs"),
            "res_model": "pdfgen.async.job",
            "view_mode": "list,form",
            "domain": domain,
            "target": "current",
        }

    def action_open_pdfgen_wizard_from_list(self):
        """Entry point for the list-view header button.

        Single record → opens the existing sync wizard.
        Multiple records → opens the async dispatch wizard which fans out
        one `/documents/generate/async` call per record and tracks them in
        `pdfgen.async.job`.
        """
        if not self:
            raise UserError(self.env._("Select at least one record."))
        if len(self) == 1:
            return self.action_open_pdfgen_wizard()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Generate custom PDFs"),
            "res_model": "pdfgen.async.dispatch.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "active_model": self._name,
                "active_ids": self.ids,
            },
        }
