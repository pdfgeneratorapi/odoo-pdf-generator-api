from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import pdfgen_resolver
from .pdfgen_api_client import DEFAULT_BASE_URL, PdfGenApiClient, PdfGenApiError


class PdfGenTemplateMapping(models.Model):
    _name = "pdfgen.template.mapping"
    _description = "pdfgeneratorapi.com template field mapping"
    _rec_name = "name"
    _order = "name"

    name = fields.Char(required=True)
    template_id = fields.Char(
        required=True,
        help="Remote pdfgeneratorapi.com template ID.",
    )
    template_name = fields.Char(
        help="Cached template name — refreshed by 'Load placeholders'.",
    )
    model_id = fields.Many2one(
        "ir.model",
        required=True,
        string="Odoo Model",
        ondelete="cascade",
        domain="[('transient', '=', False)]",
        help="The Odoo model this template renders from (e.g. account.move).",
    )
    model = fields.Char(related="model_id.model", store=True, readonly=True)
    active = fields.Boolean(default=True)
    line_ids = fields.One2many(
        "pdfgen.template.mapping.line",
        "mapping_id",
        string="Field Mappings",
    )

    _sql_constraints = [
        (
            "unique_template_id",
            "unique(template_id)",
            "A mapping for this template already exists.",
        ),
    ]

    @api.model
    def _build_client(self):
        icp = self.env["ir.config_parameter"].sudo()
        key = icp.get_param("pdfgen.api_key")
        secret = icp.get_param("pdfgen.api_secret")
        workspace = icp.get_param("pdfgen.workspace_identifier")
        if not (key and secret and workspace):
            raise UserError(
                _("PDF Generator API is not configured. Go to Settings > PDF Generator API.")
            )
        return PdfGenApiClient(
            base_url=icp.get_param("pdfgen.api_base_url") or DEFAULT_BASE_URL,
            api_key=key,
            api_secret=secret,
            workspace_identifier=workspace,
        )

    def action_load_placeholders(self):
        """Replace this mapping's lines with the flattened template-data schema."""
        self.ensure_one()
        client = self._build_client()
        try:
            response = client.get_template_data(self.template_id)
        except PdfGenApiError as e:
            raise UserError(
                _(
                    "Could not load template data (HTTP %s): %s",
                    e.status or "—",
                    (e.body or "no body")[:500],
                )
            ) from e
        data = response.get("response", response) if isinstance(response, dict) else {}
        if not isinstance(data, dict):
            raise UserError(_("Unexpected template data shape from the API."))

        self.line_ids.unlink()
        sequence = 0
        new_lines = []
        for path, kind, sample in pdfgen_resolver.flatten_placeholders(data):
            sequence += 10
            line_vals = {
                "mapping_id": self.id,
                "sequence": sequence,
                "placeholder_path": path,
                "is_list": kind == "list",
            }
            if kind == "list" and isinstance(sample, dict):
                child_vals = []
                child_seq = 0
                for child_path, child_kind, _sub in pdfgen_resolver.flatten_placeholders(sample):
                    child_seq += 10
                    child_vals.append(
                        (
                            0,
                            0,
                            {
                                "mapping_id": self.id,
                                "sequence": child_seq,
                                "placeholder_path": child_path,
                                "is_list": child_kind == "list",
                            },
                        )
                    )
                line_vals["child_ids"] = child_vals
            new_lines.append((0, 0, line_vals))
        self.write({"line_ids": new_lines})

        detail = None
        try:
            detail = client._request("GET", f"/templates/{int(self.template_id)}")
        except PdfGenApiError:
            detail = None
        if isinstance(detail, dict):
            remote = detail.get("response") or {}
            if isinstance(remote, dict) and remote.get("name"):
                self.template_name = remote["name"]

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success",
                "title": _("Placeholders loaded"),
                "message": _("Imported %s placeholder(s).", len(new_lines)),
                "sticky": False,
            },
        }

    def resolve_payload(self, record):
        """Turn an Odoo record into the JSON payload the template expects."""
        self.ensure_one()
        if record._name != self.model:
            raise UserError(
                _(
                    "This mapping targets %(expected)s but the record is %(got)s.",
                    expected=self.model,
                    got=record._name,
                )
            )
        root_lines = [_LineView(line) for line in self.line_ids if not line.parent_id]
        return pdfgen_resolver.resolve(record, root_lines)


class PdfGenTemplateMappingLine(models.Model):
    _name = "pdfgen.template.mapping.line"
    _description = "pdfgeneratorapi.com template placeholder mapping"
    _order = "sequence, id"

    mapping_id = fields.Many2one(
        "pdfgen.template.mapping",
        required=True,
        ondelete="cascade",
        index=True,
    )
    parent_id = fields.Many2one(
        "pdfgen.template.mapping.line",
        ondelete="cascade",
        index=True,
        help="For children of a list placeholder, the list line.",
    )
    child_ids = fields.One2many(
        "pdfgen.template.mapping.line",
        "parent_id",
    )

    sequence = fields.Integer(default=10)
    placeholder_path = fields.Char(
        required=True,
        help="Dotted path in the template JSON. Relative to the parent list for children.",
    )
    is_list = fields.Boolean(
        help="True when the placeholder is an array of items (repeated section).",
    )
    odoo_field_path = fields.Char(
        string="Odoo Field",
        help=(
            "Dotted Odoo attribute path from the mapping's model (or from the parent "
            "list's iterated record, for children). Leave blank to emit an empty string."
        ),
    )


class _LineView:
    """Adapter giving mapping-line records the duck-typed shape the resolver expects."""

    __slots__ = ("_line",)

    def __init__(self, line):
        self._line = line

    @property
    def placeholder_path(self):
        return self._line.placeholder_path

    @property
    def odoo_field_path(self):
        return self._line.odoo_field_path or ""

    @property
    def is_list(self):
        return self._line.is_list

    @property
    def child_lines(self):
        return [_LineView(c) for c in self._line.child_ids]
