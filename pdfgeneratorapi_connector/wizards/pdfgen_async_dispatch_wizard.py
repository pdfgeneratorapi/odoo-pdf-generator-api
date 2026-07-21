"""Multi-record generator.

Opened from a list view with `active_model` + `active_ids` in context.
Takes one of two routes depending on whether async delivery can actually
work — see `async_available`.

Async (Webhook Base URL configured), for every selected record:

  1. Build a `pdfgen.async.job` row (state=pending).
  2. Call `client.generate_async(...)` with the job's signed callback URL.
  3. Flip the row to `dispatched`, store pdfgen's returned job id.

Failed dispatches mark the row `failed` so the user sees the error in
the Async Jobs list rather than getting a vague stack trace. Afterwards
the wizard redirects to the jobs list filtered to the just-created rows.

Sync (no Webhook Base URL), for every selected record: one blocking
`/documents/generate` call, attach, promote, post to the chatter — the
single-record wizard's flow, repeated. Without a callback URL the API
has nowhere to deliver to, so dispatching async would leave every job
stuck on `dispatched` forever; generating one at a time is slower but
actually produces documents. A failing record is logged and skipped so
one bad payload doesn't cost the whole selection.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..fields import TolerantSelection
from ..models.pdfgen_api_client import PdfGenApiClient, PdfGenApiError

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
    template_id = TolerantSelection(
        selection="_selection_template_id",
        default=lambda self: self._default_template_id(),
        required=True,
    )
    dataset_id = fields.Many2one(
        "pdfgen.model.dataset",
        compute="_compute_dataset_id",
        readonly=True,
    )
    async_available = fields.Boolean(
        string="Async delivery available",
        compute="_compute_async_available",
        readonly=True,
        help=(
            "True when a Webhook Base URL is configured, so pdfgeneratorapi.com "
            "has somewhere to deliver finished documents. Without it the "
            "records are generated one at a time instead."
        ),
    )

    @api.model
    def default_get(self, fields_list: list[str]) -> dict:
        defaults = super().default_get(fields_list)
        active_model = self.env.context.get("active_model")
        active_ids = self.env.context.get("active_ids") or []
        if active_model:
            defaults.setdefault("res_model", active_model)
        if active_ids:
            defaults.setdefault("res_ids", ",".join(str(i) for i in active_ids))
        return defaults

    @api.model
    def _default_template_id(self) -> str | bool:
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
    def _compute_record_count(self) -> None:
        for wiz in self:
            wiz.record_count = len(wiz._record_ids())

    @api.depends("res_model")
    def _compute_dataset_id(self) -> None:
        for wiz in self:
            if wiz.res_model:
                wiz.dataset_id = self.env["pdfgen.model.dataset"].search(
                    [("model", "=", wiz.res_model), ("active", "=", True)],
                    limit=1,
                )
            else:
                wiz.dataset_id = False

    @api.depends_context("uid", "allowed_company_ids")
    def _compute_async_available(self) -> None:
        available = self._pdfgen_async_available()
        for wiz in self:
            wiz.async_available = available

    @api.model
    def _pdfgen_async_available(self) -> bool:
        """True when async dispatch can actually complete.

        Gated on the explicit Webhook Base URL rather than the
        `web.base.url` fallback `pdfgen.async.job.callback_url` uses: that
        fallback is whatever the browser happens to reach Odoo on
        (`http://localhost:8069` on most installs), which the API cannot
        call back to. Betting a 50-record batch on it means 50 jobs stuck
        on `dispatched`.
        """
        from ..models.pdfgen_document_mixin import pdfgen_config

        return bool(pdfgen_config(self.env, "webhook_base_url"))

    def _record_ids(self) -> list[int]:
        if not self.res_ids:
            return []
        return [int(i) for i in self.res_ids.split(",") if i.strip().isdigit()]

    @api.model
    def _build_client(self) -> PdfGenApiClient:
        from ..models.pdfgen_document_mixin import build_pdfgen_client

        return build_pdfgen_client(self.env)

    @api.model
    def _selection_template_id(self) -> list[tuple[str, str]]:
        from ..models.pdfgen_document_mixin import pdfgen_template_selection

        return pdfgen_template_selection(self.env, self._build_client)

    def action_dispatch(self) -> dict:
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

        from ..models.pdfgen_document_mixin import pdfgen_resolve_template_id

        records = self.env[self.res_model].browse(record_ids).exists()
        if not self.async_available:
            return self._generate_sync(records)

        client = self._build_client()
        template_label = dict(self._fields["template_id"]._description_selection(self.env)).get(
            self.template_id, self.template_id
        )
        # Resolve once, not per record: a Default Template is copied into the
        # account on first use, and dispatching 50 records must reuse that one
        # copy rather than mint 50 of them.
        template_id = pdfgen_resolve_template_id(self.env, client, self.template_id)

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
                    template_id=template_id,
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

    def _generate_sync(self, records: models.Model) -> dict:
        """Generate the selected records one request at a time.

        Mirrors what the single-record wizard does per record — generate,
        attach with the `pdfgen:` marker, promote to the model's canonical
        PDF, post to the chatter — so a bulk run and a one-off produce the
        same result. Per-record errors are collected rather than raised: a
        UserError mid-loop would roll back the whole batch, throwing away
        the documents that did come back.
        """
        self.ensure_one()
        mixin = self.env["pdfgen.send.mixin"]
        generated, failed = [], []
        for record in records:
            try:
                attachment = mixin._pdfgen_generate_attachment(self.template_id, record)
            except (PdfGenApiError, UserError) as e:
                _logger.warning(
                    "sync generation failed for %s(%s): %s", self.res_model, record.id, e
                )
                failed.append((record, e))
                continue
            mixin._pdfgen_promote_attachment(record, attachment)
            if hasattr(record, "message_post"):
                record.message_post(
                    body=_("Generated custom PDF via pdfgeneratorapi.com."),
                    attachment_ids=[attachment.id],
                )
            generated.append(record.id)

        if not generated:
            # Nothing survived, so there is nothing to lose by rolling back —
            # and an error dialog beats landing on an empty list.
            raise UserError(
                _(
                    "No documents were generated. First error: %s",
                    failed[0][1] if failed else _("no records to process."),
                )
            )
        return {
            "type": "ir.actions.act_window",
            "name": self._sync_result_title(len(generated), len(failed)),
            "res_model": self.res_model,
            "view_mode": "list,form",
            "domain": [("id", "in", generated)],
            "target": "current",
        }

    @staticmethod
    def _sync_result_title(done: int, failed: int) -> str:
        """Breadcrumb title for the result list — the only place a partial
        failure is visible in the UI, so it carries the counts. (A
        `display_notification` action would be tidier, but a wizard dialog
        swallows client actions; the per-record errors are in the log.)
        """
        if failed:
            return _("%(done)s generated, %(failed)s failed", done=done, failed=failed)
        return _("%s document(s) generated", done)
