from odoo import fields, models


class ResCompany(models.Model):
    """Per-company pdfgen credentials.

    Each company can point at its own pdfgeneratorapi.com workspace —
    useful for Enterprise installs where subsidiaries have separate
    pdfgen accounts. When a company leaves a field blank, the global
    `ir.config_parameter` value takes over (backward-compatible default).

    Resolution lives in `pdfgen.document.mixin._pdfgen_config()`.
    """

    _inherit = "res.company"

    pdfgen_api_base_url = fields.Char(
        string="PDF Generator API Base URL",
        help="Leave blank to inherit the global Settings value.",
    )
    pdfgen_api_key = fields.Char(
        string="PDF Generator API Key",
        help="Leave blank to inherit the global Settings value.",
    )
    pdfgen_api_secret = fields.Char(
        string="PDF Generator API Secret",
        help="Leave blank to inherit the global Settings value.",
    )
    pdfgen_workspace_identifier = fields.Char(
        string="PDF Generator Workspace Identifier",
        help="Leave blank to inherit the global Settings value.",
    )
    pdfgen_editor_web_url = fields.Char(
        string="PDF Generator Editor Web URL",
        help="Leave blank to inherit the global Settings value.",
    )
    pdfgen_webhook_secret = fields.Char(
        string="PDF Generator Webhook Secret",
        help="Leave blank to inherit the global Settings value.",
    )
    pdfgen_webhook_base_url = fields.Char(
        string="PDF Generator Webhook Base URL",
        help="Leave blank to inherit the global Settings value.",
    )
