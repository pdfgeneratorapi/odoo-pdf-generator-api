"""Wizard that generates a PDF for any record via pdfgeneratorapi.com.

Generic over the source model — the wizard is opened with
`default_res_model`/`default_res_id` in context (typically by the
`action_open_pdfgen_wizard` method on `pdfgen.document.mixin`). The payload
is built from the `pdfgen.model.dataset` bound to that model. If no dataset
exists, generation fails with a user-actionable error.
"""

import base64
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..enums import Format, Output
from ..models.pdfgen_api_client import ApiResponse, PdfGenApiClient, PdfGenApiError

_logger = logging.getLogger(__name__)


class GeneratePdfWizard(models.TransientModel):
    _name = "pdfgen.generate.wizard"
    _description = "Generate PDF via pdfgeneratorapi.com"

    res_model = fields.Char(
        string="Source Model",
        required=True,
        readonly=True,
        help="Odoo model the PDF is generated from (e.g. account.move).",
    )
    res_id = fields.Integer(
        string="Source Record",
        required=True,
        readonly=True,
    )
    res_display_name = fields.Char(
        string="Document",
        compute="_compute_res_display_name",
        readonly=True,
    )
    template_id = fields.Selection(
        selection="_selection_template_id",
        default=lambda self: self._default_template_id(),
        required=True,
    )
    auto_download = fields.Boolean(
        default=False,
        help=(
            "When True, action_generate returns an ir.actions.act_url that "
            "downloads the just-created attachment in addition to attaching "
            "it to the source record. Set via context default from the "
            "split-button's `Generate and Download` dropdown item."
        ),
    )

    @api.model
    def _default_template_id(self) -> str | bool:
        """Pre-fill from the dataset's default_template_id when the wizard
        opens — saves the user a click when they already configured a
        per-model default.
        """
        res_model = self.env.context.get("default_res_model")
        if not res_model:
            return False
        dataset = self.env["pdfgen.model.dataset"].search(
            [("model", "=", res_model), ("active", "=", True)], limit=1
        )
        return dataset.default_template_id or False

    @api.depends("res_model", "res_id")
    def _compute_res_display_name(self) -> None:
        for rec in self:
            if rec.res_model and rec.res_id and rec.res_model in self.env:
                target = self.env[rec.res_model].browse(rec.res_id)
                rec.res_display_name = target.display_name or ""
            else:
                rec.res_display_name = ""

    @api.model
    def _build_client(self) -> PdfGenApiClient:
        # Delegates to the shared helper on pdfgen.document.mixin so
        # every wizard + model reads credentials identically (per-company
        # override first, global ICP fallback).
        from ..models.pdfgen_document_mixin import build_pdfgen_client

        return build_pdfgen_client(self.env)

    @api.model
    def _selection_template_id(self) -> list[tuple[str, str]]:
        from ..models.pdfgen_document_mixin import pdfgen_template_selection

        return pdfgen_template_selection(self.env, self._build_client)

    def _target_record(self) -> models.Model:
        self.ensure_one()
        if not self.res_model or self.res_model not in self.env:
            raise UserError(_("Unknown source model: %s", self.res_model))
        return self.env[self.res_model].browse(self.res_id).exists()

    def action_generate(self) -> dict:
        self.ensure_one()
        record = self._target_record()
        if not record:
            raise UserError(_("Source record no longer exists."))
        dataset = self.env["pdfgen.model.dataset"].search(
            [("model", "=", self.res_model), ("active", "=", True)],
            limit=1,
        )
        if not dataset:
            raise UserError(
                _(
                    "No active dataset found for %s. "
                    "Create one under PDF Generator API > Field Datasets.",
                    self.res_model,
                )
            )
        from ..models.pdfgen_document_mixin import pdfgen_resolve_template_id

        client = self._build_client()
        data = dataset.resolve_payload(record)
        stem = record.display_name or record._name
        filename = f"{stem.replace('/', '_')}.pdf"
        try:
            template_id = pdfgen_resolve_template_id(self.env, client, self.template_id)
            response = client.generate(
                template_id=template_id,
                data=data,
                name=filename,
                output=Output.BASE64,
                format=Format.PDF,
            )
        except PdfGenApiError as e:
            raise UserError(
                _(
                    "PDF generation failed (HTTP %(status)s): %(body)s",
                    status=e.status or "—",
                    body=(e.body or "no body")[:500],
                )
            ) from e

        pdf_b64 = self._extract_pdf_payload(response)
        if not pdf_b64:
            raise UserError(
                _(
                    "Unexpected API response shape. Got keys: %s",
                    list(response.keys())
                    if isinstance(response, dict)
                    else type(response).__name__,
                )
            )

        try:
            base64.b64decode(pdf_b64, validate=True)
        except (ValueError, TypeError) as e:
            raise UserError(_("API returned invalid base64: %s", e)) from e

        # Honor the attachment cleanup policy before creating the fresh one.
        # Only deletes attachments we created ourselves (description starts
        # with "pdfgen:"), so manually uploaded PDFs on the same record
        # survive. The trailing res_field clause bypasses
        # ir.attachment._search's implicit res_field=False filter, since
        # we promote prior generations via res_field below.
        icp = self.env["ir.config_parameter"].sudo()
        if icp.get_param("pdfgen.attachment_cleanup", "replace") == "replace":
            self.env["ir.attachment"].search(
                [
                    ("res_model", "=", self.res_model),
                    ("res_id", "=", self.res_id),
                    ("description", "=like", "pdfgen:%"),
                    "|",
                    ("res_field", "=", False),
                    ("res_field", "!=", False),
                ]
            ).unlink()

        attachment = self.env["ir.attachment"].create(
            {
                "name": filename,
                "type": "binary",
                "datas": pdf_b64,
                "res_model": self.res_model,
                "res_id": self.res_id,
                "mimetype": "application/pdf",
                # Marker so the cleanup policy can find our attachments without
                # risking other PDFs the user manually uploaded to the record.
                "description": f"pdfgen:template:{self.template_id}",
            }
        )
        # Promote to the model's canonical-PDF binary field so the form-view
        # Document Preview pane (and the Send wizard's extras) pick up the
        # pdfgen PDF as the official report. The mixin handles the res_field
        # swap (taking the claim from any existing standard-report attachment).
        self.env["pdfgen.send.mixin"]._pdfgen_promote_attachment(record, attachment)
        # Only post to the chatter if the source model supports it.
        if hasattr(record, "message_post"):
            record.message_post(
                body=_("Generated custom PDF via pdfgeneratorapi.com."),
                attachment_ids=[attachment.id],
            )
        if self.auto_download:
            return {
                "type": "ir.actions.act_url",
                "url": f"/web/content/{attachment.id}?download=true",
                "target": "download",
            }
        # soft_reload re-fetches the current form view so the Document
        # Preview pane (which reads from invoice_pdf_report_id, computed on
        # ir.attachment.res_field) reflects the just-promoted pdfgen PDF
        # without a full browser reload.
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    @staticmethod
    def _extract_pdf_payload(response: ApiResponse) -> str | None:
        """Find the base64 payload in the API response regardless of envelope shape."""
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
