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

import base64
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.pdfgen_api_client import PdfGenApiError
from ..models.pdfgen_resolver import flatten_placeholders

_logger = logging.getLogger(__name__)


class PdfgenCoverageWizard(models.TransientModel):
    _name = "pdfgen.coverage.wizard"
    _description = "Check pdfgen template coverage against a dataset"

    dataset_id = fields.Many2one(
        "pdfgen.model.dataset",
        required=True,
        ondelete="cascade",
    )
    template_id = fields.Selection(
        selection="_selection_template_id",
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

    preview_html = fields.Html(
        string="Preview",
        readonly=True,
        sanitize=False,
        help=(
            "HTML render of the selected template with a sample record's data. "
            "Useful to sanity-check the template + dataset combination without "
            "generating a PDF."
        ),
    )
    preview_source = fields.Char(
        readonly=True,
        help="Origin of the sample payload: a real record's display name, or the API's dummy data.",
    )

    @api.model
    def _build_client(self):
        from ..models.pdfgen_document_mixin import build_pdfgen_client

        return build_pdfgen_client(self.env)

    @api.model
    def _selection_template_id(self):
        # include_library=False: the coverage check reads the template's
        # sample data via `GET /templates/{id}/data`, which only exists for
        # account templates — library templates can't be analysed here.
        from ..models.pdfgen_document_mixin import pdfgen_template_selection

        return pdfgen_template_selection(self.env, self._build_client, include_library=False)

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
                    "Could not load template data (HTTP %(status)s): %(body)s",
                    status=e.status or "—",
                    body=(e.body or "no body")[:500],
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
        return self._reopen()

    def action_preview(self):
        """Call /documents/generate with format=html and stash the result in the wizard.

        Prefers a real record of the dataset's model (so the user sees *their* data
        in the template). Falls back to the template's own sample data when no
        record exists — useful for fresh databases or models with no instances yet.
        """
        self.ensure_one()
        client = self._build_client()
        data, source = self._preview_payload(client)
        try:
            response = client.generate(
                template_id=self.template_id,
                data=data,
                name="coverage-preview",
                output="base64",
                fmt="html",
            )
        except PdfGenApiError as e:
            raise UserError(
                _(
                    "Preview failed (HTTP %(status)s): %(body)s",
                    status=e.status or "—",
                    body=(e.body or "no body")[:500],
                )
            ) from e
        html_b64 = self._extract_payload(response)
        if not html_b64:
            raise UserError(
                _(
                    "Unexpected API response for preview. Got: %s",
                    list(response.keys())
                    if isinstance(response, dict)
                    else type(response).__name__,
                )
            )
        try:
            html = base64.b64decode(html_b64).decode("utf-8", errors="replace")
        except (ValueError, TypeError) as e:
            raise UserError(_("API returned invalid base64 HTML: %s", e)) from e
        self.write({"preview_html": html, "preview_source": source})
        return self._reopen()

    def _preview_payload(self, client):
        """Resolve the payload for the preview call.

        Returns a `(data, source_label)` tuple. Tries a real record first, falls
        back to `/templates/{id}/data` sample payload.
        """
        dataset = self.dataset_id
        if dataset.model and dataset.model in self.env:
            record = self.env[dataset.model].search([], limit=1)
            if record:
                try:
                    return dataset.resolve_payload(record), record.display_name or dataset.model
                except Exception as e:
                    _logger.warning(
                        "preview: resolve_payload failed for %s(%s): %s",
                        dataset.model,
                        record.id,
                        e,
                    )
        try:
            response = client.get_template_data(self.template_id)
        except PdfGenApiError as e:
            raise UserError(
                _(
                    "Could not load sample template data (HTTP %(status)s): %(body)s",
                    status=e.status or "—",
                    body=(e.body or "no body")[:500],
                )
            ) from e
        data = response.get("response", {}) if isinstance(response, dict) else {}
        if not isinstance(data, dict):
            data = {}
        return data, _("API sample data")

    def _reopen(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    @staticmethod
    def _extract_payload(response):
        """Pull the base64 string out of a pdfgen /generate response."""
        if isinstance(response, str):
            return response
        if not isinstance(response, dict):
            return None
        for key in ("response", "data", "base64"):
            value = response.get(key)
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                for sub_key in ("base64", "content", "data"):
                    if isinstance(value.get(sub_key), str):
                        return value[sub_key]
        return None
