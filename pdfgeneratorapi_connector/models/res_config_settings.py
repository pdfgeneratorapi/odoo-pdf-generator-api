import secrets

from odoo import _, api, fields, models, release
from odoo.exceptions import UserError

from .pdfgen_api_client import DEFAULT_BASE_URL, PdfGenApiClient, PdfGenApiError

# The core addon. Bridges are `<core>_<doc type>`, so this doubles as the
# prefix for finding every installed pdfgen module.
PDFGEN_CORE_MODULE = "pdfgeneratorapi_connector"


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
        help=(
            "Your account email for regular workspaces, or a sub-workspace "
            "identifier (format per the pdfgeneratorapi.com workspace settings "
            "page — typically `parent@domain.com:slug`) for sub-workspaces. "
            "The value is forwarded verbatim into the JWT `sub` claim so pdfgen "
            "routes requests to the right workspace."
        ),
    )
    pdfgen_attachment_cleanup = fields.Selection(
        selection=[
            ("keep", "Keep all versions"),
            ("replace", "Replace previous pdfgen PDFs on the record"),
        ],
        string="Attachment cleanup",
        config_parameter="pdfgen.attachment_cleanup",
        default="replace",
        help=(
            "What to do with previously-generated PDFs on the same record when "
            "the user clicks Generate again. `Replace` deletes pdfgen-generated "
            "PDFs on that record before attaching the new one (default); `Keep` "
            "leaves every version attached. Only attachments created by this "
            "connector are affected — manually uploaded PDFs are never touched."
        ),
    )
    pdfgen_show_secret = fields.Boolean(
        string="Show secret",
        default=False,
        help="Toggle to reveal the API secret in plaintext.",
    )
    pdfgen_show_webhook_secret = fields.Boolean(
        string="Show webhook secret",
        default=False,
        help="Toggle to reveal the webhook secret in plaintext.",
    )
    pdfgen_webhook_base_url = fields.Char(
        string="Webhook Base URL",
        config_parameter="pdfgen.webhook_base_url",
        help=(
            "Public base URL of this Odoo (scheme + host) so pdfgeneratorapi.com "
            "can call back when an async job finishes — e.g. "
            "https://odoo.example.com or an ngrok tunnel for local dev. Leave "
            "blank to fall back to the System Parameter `web.base.url`."
        ),
    )
    pdfgen_webhook_secret = fields.Char(
        string="Webhook Secret",
        config_parameter="pdfgen.webhook_secret",
        help=(
            "Shared secret used to sign each async job's callback URL. Auto-"
            "filled with a random value on first save if left blank — only "
            "rotate it if you suspect leakage. The same secret derives the "
            "per-job token the webhook receiver verifies before accepting a "
            "delivery."
        ),
    )

    # Bridge module toggles. Odoo's res.config.settings recognises the
    # `module_<name>` prefix: `default_get` reads each field from
    # `ir.module.module.state == 'installed'`, and `execute()` calls
    # button_immediate_install/uninstall on Save — including transitive deps
    # (e.g. ticking Rental pulls in the Sales bridge and `sale_renting`).
    module_pdfgeneratorapi_connector_account = fields.Boolean(
        string="Invoices & Credit Notes",
        help=(
            "Adds a Generate custom PDF button on account.move (customer "
            "invoices, vendor bills, credit notes) and a Use pdfgen PDF "
            "toggle on the invoice Send wizard. Installs the Accounting app "
            "if not already present. Seeds a default placeholder dataset."
        ),
    )
    module_pdfgeneratorapi_connector_sale = fields.Boolean(
        string="Quotations & Sale Orders",
        help=(
            "Adds a Generate custom PDF button on sale.order and seeds a "
            "default placeholder dataset. Installs the Sales app if not "
            "already present. Unticking removes the bridge and its dataset; "
            "templates on pdfgeneratorapi.com are untouched."
        ),
    )
    module_pdfgeneratorapi_connector_purchase = fields.Boolean(
        string="Purchase Orders",
        help=(
            "Adds a Generate custom PDF button on purchase.order and seeds a "
            "default placeholder dataset. Installs the Purchase app if not "
            "already present."
        ),
    )
    module_pdfgeneratorapi_connector_stock = fields.Boolean(
        string="Delivery Slips & Receipts",
        help=(
            "Adds a Generate custom PDF button on stock.picking and seeds a "
            "default placeholder dataset. Installs the Inventory app if not "
            "already present."
        ),
    )
    module_pdfgeneratorapi_connector_mrp = fields.Boolean(
        string="Manufacturing Orders",
        help=(
            "Adds a Generate custom PDF button on mrp.production and seeds a "
            "default placeholder dataset. Installs the Manufacturing app if "
            "not already present."
        ),
    )
    module_pdfgeneratorapi_connector_rental = fields.Boolean(
        string="Rental Orders",
        help=(
            "Adds a rental-specific dataset on top of the Sales bridge. "
            "Ticking this also enables the Sales bridge and installs the "
            "Rental app if not already present."
        ),
    )

    pdfgen_module_version = fields.Char(
        string="Connector version",
        readonly=True,
        default=lambda self: self._pdfgen_installed_version(),
        help=(
            "Installed version of the PDF Generator API connector, followed by "
            "each installed document bridge. Quote this when contacting "
            "support@pdfgeneratorapi.com so we know exactly which release you "
            "are running."
        ),
    )

    def _pdfgen_installed_version(self):
        """Installed versions of the connector and every document bridge.

        Reports the bridges too, not just the core module: they carry their
        own manifest versions, so a core version alone cannot answer "is this
        deployment current?" — a fix living entirely in a bridge (a view, a
        dataset) moves no core digit at all.

        Bridge versions are printed without the series prefix the core version
        already states ("19.0.1.0.2" -> "1.0.2"). A bridge on a *different*
        series is printed in full: that is a broken deployment, and hiding it
        is exactly the failure this line exists to catch.

        Deliberately a default rather than a compute. The web client opens
        Settings as a *new* record and fills the form via default_get()/
        onchange(); on Odoo <= 18 those paths stamp False into the cache for
        every fields_spec entry default_get does not supply, and a non-stored
        computed field with no @api.depends is never invalidated afterwards, so
        it reached the browser as False and the footer rendered blank. Odoo 19
        fixed this upstream ("don't assign computed fields without
        dependencies" in web/models/models.py); 17 and 18 did not. A default is
        supplied by default_get on every version.
        """
        modules = (
            self.env["ir.module.module"]
            .sudo()
            .search(
                [
                    ("name", "=like", f"{PDFGEN_CORE_MODULE}%"),
                    ("state", "=", "installed"),
                ]
            )
        )
        core = modules.filtered(lambda m: m.name == PDFGEN_CORE_MODULE)
        core_version = core.latest_version if core else ""
        if not core_version:
            return _("unknown")
        # "19.0.7.1.4" -> "19.0." — the series the bridges are expected to share.
        series = ".".join(core_version.split(".")[:2]) + "."
        bridges = []
        for module in modules.sorted("name"):
            if module.name == PDFGEN_CORE_MODULE or not module.latest_version:
                continue
            label = module.name[len(PDFGEN_CORE_MODULE) + 1 :]
            version = module.latest_version
            if version.startswith(series):
                version = version[len(series) :]
            bridges.append(f"{label} {version}")
        if not bridges:
            return core_version
        joined = ", ".join(bridges)
        return f"{core_version} ({joined})"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._pdfgen_ensure_webhook_secret()
        return records

    def write(self, vals):
        result = super().write(vals)
        if "pdfgen_webhook_secret" in vals:
            self._pdfgen_ensure_webhook_secret()
        return result

    def _pdfgen_ensure_webhook_secret(self):
        """Mint a random webhook secret on first save when admin left it blank.

        Stored on the same `pdfgen.webhook_secret` ICP key the field is
        bound to. `set_param` is sudo-safe and idempotent.
        """
        icp = self.env["ir.config_parameter"].sudo()
        if not icp.get_param("pdfgen.webhook_secret"):
            icp.set_param("pdfgen.webhook_secret", secrets.token_urlsafe(32))

    def _get_pdfgen_client(self):
        """Build a client from what's on the unsaved Settings form.

        Note this reads the transient wizard's values (pre-save), not the
        stored ICP — that lets Test Connection work before the user clicks
        Save. Multi-company credential resolution happens at generate-time
        via pdfgen.document.mixin.build_pdfgen_client().
        """
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
            partner_id=f"odoo_v{release.version_info[0]}",
        )

    def action_pdfgen_test_connection(self):
        client = self._get_pdfgen_client()
        try:
            client.ping()
        except PdfGenApiError as e:
            raise UserError(
                _(
                    "Connection failed (HTTP %(status)s): %(body)s",
                    status=e.status or "—",
                    body=(e.body or "no body")[:500],
                )
            ) from e
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("PDF Generator API"),
                "message": _("Connected to workspace: %s", self.pdfgen_workspace_identifier),
                "sticky": False,
            },
        }
