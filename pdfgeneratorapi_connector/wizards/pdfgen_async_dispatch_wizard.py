"""Multi-record dispatcher.

Opened from a list view with `active_model` + `active_ids` in context.
For every selected record:

  1. Build a `pdfgen.async.job` row (state=pending).
  2. Call `client.generate_async(...)` with the job's signed callback URL.
  3. Flip the row to `dispatched`, store pdfgen's returned job id.

Failed dispatches mark the row `failed` so the user sees the error in
the Async Jobs list rather than getting a vague stack trace.

After dispatch the wizard redirects to the jobs list filtered to the
just-created rows.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.pdfgen_api_client import PdfGenApiError

_logger = logging.getLogger(__name__)


class PdfgenAsyncDispatchWizard(models.TransientModel):
    _name = "pdfgen.async.dispatch.wizard"
    _description = "Dispatch async PDF generation for selected records"

    res_model = fields.Char(required=True, readonly=True)
    res_ids = fields.Char(
        required=True,
        readonly=True,
        help="JSON-ish list of selected record ids, comma-separated.",
    )
    record_count = fields.Integer(
        compute="_compute_record_count",
        readonly=True,
    )
    template_id = fields.Selection(
        selection="_selection_template_id",
        default=lambda self: self._default_template_id(),
        required=True,
    )
    dataset_id = fields.Many2one(
        "pdfgen.model.dataset",
        compute="_compute_dataset_id",
        readonly=True,
    )

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        active_model = self.env.context.get("active_model")
        active_ids = self.env.context.get("active_ids") or []
        if active_model:
            defaults.setdefault("res_model", active_model)
        if active_ids:
            defaults.setdefault("res_ids", ",".join(str(i) for i in active_ids))
        return defaults

    @api.model
    def _default_template_id(self):
        # Pre-fill template_id from the dataset's default_template_id when
        # the wizard opens — list-view dispatch sets active_model in context.
        active_model = self.env.context.get("active_model")
        if not active_model:
            return False
        dataset = self.env["pdfgen.model.dataset"].search(
            [("model", "=", active_model), ("active", "=", True)], limit=1
        )
        return dataset.default_template_id or False

    @api.depends("res_ids")
    def _compute_record_count(self):
        for wiz in self:
            wiz.record_count = len(wiz._record_ids())

    @api.depends("res_model")
    def _compute_dataset_id(self):
        for wiz in self:
            if wiz.res_model:
                wiz.dataset_id = self.env["pdfgen.model.dataset"].search(
                    [("model", "=", wiz.res_model), ("active", "=", True)],
                    limit=1,
                )
            else:
                wiz.dataset_id = False

    def _record_ids(self):
        if not self.res_ids:
            return []
        return [int(i) for i in self.res_ids.split(",") if i.strip().isdigit()]

    @api.model
    def _build_client(self):
        from ..models.pdfgen_document_mixin import build_pdfgen_client

        return build_pdfgen_client(self.env)

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

    def action_dispatch(self):
        self.ensure_one()
        if not self.res_model or self.res_model not in self.env:
            raise UserError(_("Unknown source model: %s", self.res_model))
        if not self.dataset_id:
            raise UserError(
                _(
                    "No active dataset found for %s. "
                    "Create one under PDF Generator API > Field Datasets.",
                    self.res_model,
                )
            )
        record_ids = self._record_ids()
        if not record_ids:
            raise UserError(_("No records selected."))

        records = self.env[self.res_model].browse(record_ids).exists()
        client = self._build_client()
        template_label = dict(self._fields["template_id"]._description_selection(self.env)).get(
            self.template_id, self.template_id
        )

        created_ids = []
        for record in records:
            job = self.env["pdfgen.async.job"].create(
                {
                    "name": f"{template_label} — {record.display_name or record.id}",
                    "template_id": self.template_id,
                    "template_name": template_label,
                    "res_model": self.res_model,
                    "res_id": record.id,
                    "dataset_id": self.dataset_id.id,
                }
            )
            created_ids.append(job.id)
            try:
                data = self.dataset_id.resolve_payload(record)
                pdfgen_job_id = client.generate_async(
                    template_id=self.template_id,
                    data=data,
                    callback_url=job.callback_url(),
                    name=f"{(record.display_name or record._name).replace('/', '_')}.pdf",
                )
            except (PdfGenApiError, UserError) as e:
                _logger.warning(
                    "async dispatch failed for %s(%s): %s", self.res_model, record.id, e
                )
                job.write({"state": "failed", "error": str(e)})
                continue
            except Exception as e:
                _logger.exception("async dispatch crashed for %s(%s)", self.res_model, record.id)
                job.write({"state": "failed", "error": str(e)})
                continue
            job.write(
                {
                    "state": "dispatched",
                    "pdfgen_job_id": pdfgen_job_id or "",
                    "dispatched_at": fields.Datetime.now(),
                }
            )

        return {
            "type": "ir.actions.act_window",
            "name": _("Async Jobs"),
            "res_model": "pdfgen.async.job",
            "view_mode": "list,form",
            "domain": [("id", "in", created_ids)],
            "target": "current",
        }
