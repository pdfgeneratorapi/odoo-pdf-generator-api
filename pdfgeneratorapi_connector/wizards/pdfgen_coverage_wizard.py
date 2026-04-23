"""Wizard that diffs a pdfgen template's placeholder schema against a dataset.

Given a `pdfgen.model.dataset` and a remote template ID, fetches the template's
sample data via `GET /templates/{id}/data`, flattens it into placeholder paths
(including list-item children as ``<list>[].<child>``), and reports which paths
the dataset maps (matched), which the template needs but the dataset doesn't
provide (missing), and which the dataset declares but the template doesn't use
(extra).

The selection + client-build methods duplicate what's in generate_pdf_wizard.py
on purpose — two callers don't yet justify a mixin. If a third caller appears,
extract into a shared helper.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.pdfgen_api_client import (
    DEFAULT_BASE_URL,
    PdfGenApiClient,
    PdfGenApiError,
)
from ..models.pdfgen_resolver import flatten_placeholders

_logger = logging.getLogger(__name__)


class PdfgenCoverageWizard(models.TransientModel):
    _name = "pdfgen.coverage.wizard"
    _description = "Check pdfgen template coverage against a dataset"

    dataset_id = fields.Many2one(
        "pdfgen.model.dataset",
        string="Dataset",
        required=True,
        ondelete="cascade",
    )
    template_id = fields.Selection(
        selection="_selection_template_id",
        string="Template",
        required=True,
    )
    template_name = fields.Char(readonly=True)

    checked = fields.Boolean(readonly=True)
    coverage_total = fields.Integer(string="Total placeholders", readonly=True)
    coverage_matched = fields.Integer(string="Matched", readonly=True)
    missing_placeholders = fields.Text(
        string="Missing from dataset",
        readonly=True,
        help="Paths the template references but the dataset doesn't map.",
    )
    extra_placeholders = fields.Text(
        string="Unused by template",
        readonly=True,
        help="Paths the dataset maps but this template doesn't reference.",
    )

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

    @api.model
    def _selection_template_id(self):
        try:
            client = self._build_client()
        except UserError:
            return []
        try:
            response = client.list_templates(per_page=100)
        except PdfGenApiError as e:
            _logger.warning("list_templates failed: %s / %s", e.status, e.body)
            return []
        templates = response.get("response", response) if isinstance(response, dict) else response
        if not isinstance(templates, list):
            return []
        result = []
        for t in templates:
            tid = t.get("id")
            name = t.get("name") or f"Template {tid}"
            if tid is None:
                continue
            result.append((str(tid), name))
        return result

    def _template_placeholder_paths(self, data):
        """Flatten the template-data response into a set of canonical paths.

        Scalars → their dotted path. List rows → `<path>[].<child>` for each
        placeholder the sample item exposes.
        """
        paths = set()
        if not isinstance(data, dict):
            return paths
        for path, kind, sample in flatten_placeholders(data):
            if kind == "list":
                if isinstance(sample, dict):
                    for child_path, _child_kind, _sub in flatten_placeholders(sample):
                        paths.add(f"{path}[].{child_path}")
                else:
                    paths.add(path)
            else:
                paths.add(path)
        return paths

    def _dataset_placeholder_paths(self):
        """Same shape as _template_placeholder_paths, built from this dataset's lines."""
        paths = set()
        for line in self.dataset_id.line_ids:
            if line.parent_id:
                continue
            if line.is_list:
                children = line.child_ids
                if children:
                    for child in children:
                        paths.add(f"{line.placeholder_path}[].{child.placeholder_path}")
                else:
                    paths.add(line.placeholder_path)
            else:
                paths.add(line.placeholder_path)
        return paths

    def action_check(self):
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
        if isinstance(data, list) and not data:
            data = {}

        template_paths = self._template_placeholder_paths(data)
        dataset_paths = self._dataset_placeholder_paths()

        missing = sorted(template_paths - dataset_paths)
        extra = sorted(dataset_paths - template_paths)
        matched = template_paths & dataset_paths

        # Best-effort cache of the template's display name so the form view
        # shows something friendlier than the numeric id after the check.
        # Any failure here (non-numeric id, network hiccup, unexpected shape)
        # is silently swallowed — the coverage numbers are the important result.
        display_name = ""
        try:
            detail = client._request("GET", f"/templates/{int(self.template_id)}")
            if isinstance(detail, dict):
                remote = detail.get("response") or {}
                if isinstance(remote, dict):
                    display_name = remote.get("name") or ""
        except (PdfGenApiError, ValueError, TypeError):
            pass

        self.write(
            {
                "checked": True,
                "coverage_total": len(template_paths),
                "coverage_matched": len(matched),
                "missing_placeholders": "\n".join(missing),
                "extra_placeholders": "\n".join(extra),
                "template_name": display_name,
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
