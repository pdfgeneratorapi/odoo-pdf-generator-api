"""Receiver for pdfgeneratorapi.com async-generation callbacks.

URL contract:

    POST /pdfgen/webhook/deliver?j=<job_id>&t=<token>

The receiver:

  1. Looks up `pdfgen.async.job` row `j`. Unknown id → unknown-job ack.
  2. Verifies `t` matches `HMAC(webhook_secret, job_id)`. Mismatch →
     bad-token ack. This is the only auth — pdfgen has no Odoo session,
     so we can't rely on cookies / login.
  3. Decodes the base64 PDF out of the body, attaches it to the source
     record, marks the job completed.
  4. Returns a JSON ack so pdfgen doesn't retry on 5xx.

Idempotent: a second delivery on a `completed` job is a no-op (pdfgen
retries on transient receiver errors).

Uses `type="http"` (raw HTTP) instead of `type="json"` because pdfgen
posts a plain JSON body, not Odoo's JSON-RPC envelope. We parse the
body ourselves.
"""

import base64
import json
import logging

from odoo import _, fields, http
from odoo.exceptions import UserError
from odoo.http import request

from ..wizards.generate_pdf_wizard import GeneratePdfWizard

_logger = logging.getLogger(__name__)


def _ack(payload, status=200):
    return request.make_response(
        json.dumps(payload),
        headers=[("Content-Type", "application/json")],
        status=status,
    )


class PdfgenWebhookController(http.Controller):
    @http.route(
        "/pdfgen/webhook/deliver",
        type="http",
        auth="public",
        csrf=False,
        methods=["POST"],
        save_session=False,
    )
    # pylint: disable=too-many-return-statements,too-many-locals
    def deliver(self, **kwargs):
        params = request.httprequest.args
        job_id = params.get("j") or kwargs.get("j")
        token = params.get("t") or kwargs.get("t")
        try:
            job_id_int = int(job_id) if job_id is not None else None
        except (TypeError, ValueError):
            job_id_int = None
        if not job_id_int or not token:
            _logger.warning("webhook delivery missing job id or token")
            return _ack({"status": "error", "reason": "missing-params"}, status=400)

        Job = request.env["pdfgen.async.job"].sudo()
        job = Job.browse(job_id_int).exists()
        if not job:
            _logger.warning("webhook delivery for unknown job %s", job_id_int)
            return _ack({"status": "error", "reason": "unknown-job"}, status=404)
        if not job.verify_token(token):
            _logger.warning("webhook delivery for job %s rejected: bad token", job.id)
            return _ack({"status": "error", "reason": "bad-token"}, status=403)

        if job.state == "completed":
            return _ack({"status": "ok", "note": "already-completed"})

        raw = request.httprequest.get_data(as_text=True) or ""
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        # Cross-check pdfgen's job id when the webhook body carries one — guards
        # against an attacker replaying a delivery against the wrong job row.
        body_job_id = payload.get("id") or payload.get("job_id")
        if body_job_id and job.pdfgen_job_id and str(body_job_id) != str(job.pdfgen_job_id):
            _logger.warning(
                "webhook delivery for job %s rejected: pdfgen id mismatch (%s vs %s)",
                job.id,
                body_job_id,
                job.pdfgen_job_id,
            )
            return _ack({"status": "error", "reason": "pdfgen-id-mismatch"}, status=403)

        if payload.get("error") or payload.get("status") in ("failed", "error"):
            error = str(payload.get("error") or payload.get("message") or payload)[:2000]
            job.write({"state": "failed", "error": error, "completed_at": fields.Datetime.now()})
            return _ack({"status": "ok"})

        pdf_b64 = GeneratePdfWizard._extract_pdf_payload(payload)
        if not pdf_b64:
            keys = list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__
            job.write(
                {
                    "state": "failed",
                    "error": _(
                        "Webhook delivered no recognisable PDF payload. Got keys: %s",
                        keys,
                    ),
                    "completed_at": fields.Datetime.now(),
                }
            )
            return _ack({"status": "error", "reason": "no-payload"}, status=400)
        try:
            base64.b64decode(pdf_b64, validate=True)
        except (ValueError, TypeError) as e:
            job.write(
                {
                    "state": "failed",
                    "error": _("Invalid base64 from PDF API: %s", e),
                    "completed_at": fields.Datetime.now(),
                }
            )
            return _ack({"status": "error", "reason": "bad-base64"}, status=400)

        try:
            attachment = _attach_pdf(job, pdf_b64)
        except UserError as e:
            job.write({"state": "failed", "error": str(e), "completed_at": fields.Datetime.now()})
            return _ack({"status": "error", "reason": "attach-failed"}, status=500)

        job.write(
            {
                "state": "completed",
                "attachment_id": attachment.id,
                "completed_at": fields.Datetime.now(),
            }
        )
        return _ack({"status": "ok", "attachment_id": attachment.id})


def _attach_pdf(job, pdf_b64):
    """Create the ir.attachment + chatter post for a delivered PDF.

    Honours the same Replace/Keep cleanup policy + `pdfgen:` description
    marker the sync wizard uses, so the two flows interoperate cleanly.
    """
    env = job.env
    if not (job.res_model and job.res_id and job.res_model in env):
        raise UserError(_("Source record no longer exists for job %s.", job.id))
    record = env[job.res_model].sudo().browse(job.res_id).exists()
    if not record:
        raise UserError(_("Source record no longer exists for job %s.", job.id))

    icp = env["ir.config_parameter"].sudo()
    if icp.get_param("pdfgen.attachment_cleanup", "replace") == "replace":
        env["ir.attachment"].sudo().search(
            [
                ("res_model", "=", job.res_model),
                ("res_id", "=", job.res_id),
                ("description", "=like", "pdfgen:%"),
            ]
        ).unlink()

    stem = record.display_name or record._name
    filename = f"{stem.replace('/', '_')}.pdf"
    attachment = (
        env["ir.attachment"]
        .sudo()
        .create(
            {
                "name": filename,
                "type": "binary",
                "datas": pdf_b64,
                "res_model": job.res_model,
                "res_id": job.res_id,
                "mimetype": "application/pdf",
                "description": f"pdfgen:template:{job.template_id}",
            }
        )
    )
    if hasattr(record, "message_post"):
        record.sudo().message_post(
            body=_("Generated custom PDF via pdfgeneratorapi.com (async)."),
            attachment_ids=[attachment.id],
        )
    return attachment
