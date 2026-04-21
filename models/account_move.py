from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

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
            "name": "Generate custom PDF",
            "res_model": "pdfgen.generate.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_move_id": self.id},
        }
