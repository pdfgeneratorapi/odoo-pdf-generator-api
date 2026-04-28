"""Async generation job tracker.

Each row represents one outstanding (or finished) `POST /documents/generate/async`
request — minted by `pdfgen.async.dispatch.wizard` for every selected record,
flipped to `dispatched` once pdfgen returns a job id, and to `completed` /
`failed` when the webhook controller delivers (or rejects) the callback.

The model owns the per-job callback URL minting + token verification logic
so the controller stays thin and the dispatcher / receiver share one source
of truth for the HMAC contract.
"""

import hashlib
import hmac
import logging
from urllib.parse import urlencode

from odoo import _, api, fields, models

from .pdfgen_document_mixin import pdfgen_config

_logger = logging.getLogger(__name__)


class PdfgenAsyncJob(models.Model):
    _name = "pdfgen.async.job"
    _description = "PDF Generator async generation job"
    _order = "create_date desc, id desc"
    _rec_name = "name"

    name = fields.Char(required=True, readonly=True)
    state = fields.Selection(
        selection=[
            ("pending", "Pending"),
            ("dispatched", "Dispatched"),
            ("completed", "Completed"),
            ("failed", "Failed"),
        ],
        default="pending",
        required=True,
        readonly=True,
        index=True,
    )
    template_id = fields.Char(
        string="Template ID",
        required=True,
        readonly=True,
        help="The pdfgeneratorapi.com template id this job renders.",
    )
    template_name = fields.Char(readonly=True)
    res_model = fields.Char(required=True, readonly=True, index=True)
    res_id = fields.Integer(required=True, readonly=True, index=True)
    res_display = fields.Char(
        compute="_compute_res_display",
        help="Display name of the source record at read time.",
    )
    dataset_id = fields.Many2one(
        "pdfgen.model.dataset",
        readonly=True,
        ondelete="set null",
        help="Dataset whose resolved payload was sent to pdfgen at dispatch.",
    )
    pdfgen_job_id = fields.Char(
        string="pdfgen Job ID",
        readonly=True,
        index=True,
        help="Identifier returned by pdfgeneratorapi.com's async endpoint.",
    )
    error = fields.Text(readonly=True)
    attachment_id = fields.Many2one(
        "ir.attachment",
        readonly=True,
        ondelete="set null",
        help="Attachment created when the webhook delivered the finished PDF.",
    )
    dispatched_at = fields.Datetime(readonly=True)
    completed_at = fields.Datetime(readonly=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        readonly=True,
    )

    @api.depends("res_model", "res_id")
    def _compute_res_display(self):
        for job in self:
            if not (job.res_model and job.res_id and job.res_model in self.env):
                job.res_display = ""
                continue
            record = self.env[job.res_model].browse(job.res_id).exists()
            job.res_display = record.display_name if record else ""

    def callback_url(self):
        """Return the public URL pdfgen should call back when this job finishes.

        Embeds an HMAC-derived token in the query string so the receiver can
        reject deliveries that don't match a known job. The controller side
        recomputes the same HMAC and `compare_digest`s it.
        """
        self.ensure_one()
        base = self._pdfgen_webhook_base_url()
        token = self._pdfgen_token()
        query = urlencode({"j": self.id, "t": token})
        return f"{base.rstrip('/')}/pdfgen/webhook/deliver?{query}"

    def verify_token(self, token):
        """True iff `token` matches the HMAC we'd mint for this job right now."""
        self.ensure_one()
        if not token:
            return False
        expected = self._pdfgen_token()
        return hmac.compare_digest(expected.encode("utf-8"), token.encode("utf-8"))

    def _pdfgen_token(self):
        secret = pdfgen_config(self.env, "webhook_secret")
        if not secret:
            raise self._token_error()
        return hmac.new(
            secret.encode("utf-8"),
            str(self.id).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _pdfgen_webhook_base_url(self):
        base = pdfgen_config(self.env, "webhook_base_url")
        if not base:
            base = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        if not base:
            raise self._token_error()
        return base

    @staticmethod
    def _token_error():
        from odoo.exceptions import UserError

        return UserError(
            _(
                "PDF Generator webhook is not configured. Set the Webhook Base URL "
                "and Webhook Secret in Settings > PDF Generator API."
            )
        )
