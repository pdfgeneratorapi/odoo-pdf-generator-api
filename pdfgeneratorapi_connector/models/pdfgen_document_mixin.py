"""Abstract mixin that any document model can inherit to expose the
`Generate custom PDF` button + wizard flow. Bridge modules (e.g.
pdfgeneratorapi_connector_sale) just `_inherit` the mixin on the target
model and add a view to surface the button.
"""

from odoo import _, api, fields, models


class PdfgenDocumentMixin(models.AbstractModel):
    _name = "pdfgen.document.mixin"
    _description = "Expose the PDF Generator wizard on a document model"

    pdfgen_configured = fields.Boolean(
        compute="_compute_pdfgen_configured",
        help="True when PDF Generator API credentials are present.",
    )

    @api.depends_context("uid")
    def _compute_pdfgen_configured(self):
        icp = self.env["ir.config_parameter"].sudo()
        ready = bool(
            icp.get_param("pdfgen.api_key")
            and icp.get_param("pdfgen.api_secret")
            and icp.get_param("pdfgen.workspace_identifier")
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
