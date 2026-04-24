from odoo import _, fields, models
from odoo.exceptions import UserError

from .pdfgen_api_client import DEFAULT_BASE_URL, PdfGenApiClient, PdfGenApiError


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    pdfgen_api_base_url = fields.Char(
        string="API Base URL",
        config_parameter="pdfgen.api_base_url",
        default=DEFAULT_BASE_URL,
        help="Regional endpoint. Default: us1.pdfgeneratorapi.com.",
    )
    pdfgen_editor_web_url = fields.Char(
        string="Editor Web URL",
        config_parameter="pdfgen.editor_web_url",
        help=(
            "Browser-facing URL for the template editor (without /api/vN). "
            "Leave empty to derive from API Base URL — correct for regular "
            "pdfgeneratorapi.com users. Set this only when Odoo reaches the "
            "API via a hostname the browser can't resolve (e.g. a Docker-"
            "internal service name or a private VPC endpoint)."
        ),
    )
    pdfgen_api_key = fields.Char(
        string="API Key",
        config_parameter="pdfgen.api_key",
        help="Public API key from your pdfgeneratorapi.com account.",
    )
    pdfgen_api_secret = fields.Char(
        string="API Secret",
        config_parameter="pdfgen.api_secret",
        help="Used to sign JWT tokens. Keep this confidential.",
    )
    pdfgen_workspace_identifier = fields.Char(
        string="Workspace Identifier",
        config_parameter="pdfgen.workspace_identifier",
        help="Your account email for regular workspaces, or the sub-workspace "
        "identifier for sub-workspaces.",
    )
    pdfgen_show_secret = fields.Boolean(
        string="Show secret",
        default=False,
        help="Toggle to reveal the API secret in plaintext.",
    )

    def _get_pdfgen_client(self):
        self.ensure_one()
        missing = [
            label
            for label, value in [
                ("API Key", self.pdfgen_api_key),
                ("API Secret", self.pdfgen_api_secret),
                ("Workspace Identifier", self.pdfgen_workspace_identifier),
            ]
            if not value
        ]
        if missing:
            raise UserError(
                _(
                    "Please fill in: %s",
                    ", ".join(missing),
                )
            )
        return PdfGenApiClient(
            base_url=self.pdfgen_api_base_url or DEFAULT_BASE_URL,
            api_key=self.pdfgen_api_key,
            api_secret=self.pdfgen_api_secret,
            workspace_identifier=self.pdfgen_workspace_identifier,
            editor_web_url=self.pdfgen_editor_web_url or None,
        )

    def action_pdfgen_test_connection(self):
        client = self._get_pdfgen_client()
        try:
            workspace = client.ping()
        except PdfGenApiError as e:
            raise UserError(
                _(
                    "Connection failed (HTTP %s): %s",
                    e.status or "—",
                    (e.body or "no body")[:500],
                )
            ) from e
        name = ""
        if isinstance(workspace, dict):
            name = (
                workspace.get("response", {}).get("name")
                or workspace.get("name")
                or workspace.get("identifier")
                or ""
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("PDF Generator API"),
                "message": _(
                    "Connected to workspace: %s", name or self.pdfgen_workspace_identifier
                ),
                "sticky": False,
            },
        }
