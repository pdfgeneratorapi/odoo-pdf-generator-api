"""Wizard that generates a PDF for an invoice via pdfgeneratorapi.com.

v1 slice: invoice-only, hardcoded serializer, synchronous generation. The
wizard pulls the template list live from the API each time it opens (no
local registry) and saves the returned PDF as ir.attachment.
"""
import base64
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.pdfgen_api_client import (
    DEFAULT_BASE_URL,
    PdfGenApiClient,
    PdfGenApiError,
)

_logger = logging.getLogger(__name__)


def _serialize_partner(partner):
    if not partner:
        return {}
    return {
        "name": partner.name or "",
        "street": partner.street or "",
        "street2": partner.street2 or "",
        "city": partner.city or "",
        "zip": partner.zip or "",
        "state": partner.state_id.name or "",
        "country": partner.country_id.name or "",
        "country_code": partner.country_id.code or "",
        "vat": partner.vat or "",
        "email": partner.email or "",
        "phone": partner.phone or "",
    }


def _serialize_invoice(move):
    return {
        "invoice_number": move.name or "",
        "invoice_date": move.invoice_date and move.invoice_date.isoformat() or "",
        "due_date": move.invoice_date_due and move.invoice_date_due.isoformat() or "",
        "state": move.state,
        "currency": {
            "code": move.currency_id.name or "",
            "symbol": move.currency_id.symbol or "",
            "position": move.currency_id.position or "",
        },
        "company": _serialize_partner(move.company_id.partner_id),
        "customer": _serialize_partner(move.partner_id),
        "lines": [
            {
                "description": line.name or "",
                "quantity": line.quantity,
                "uom": line.product_uom_id.name or "",
                "price_unit": line.price_unit,
                "discount": line.discount,
                "tax_labels": line.tax_ids.mapped("name"),
                "price_subtotal": line.price_subtotal,
                "price_total": line.price_total,
            }
            for line in move.invoice_line_ids.filtered(
                lambda l: l.display_type == "product"
            )
        ],
        "totals": {
            "untaxed": move.amount_untaxed,
            "tax": move.amount_tax,
            "total": move.amount_total,
            "residual": move.amount_residual,
        },
        "payment_reference": move.payment_reference or "",
        "narration": (move.narration or "") if isinstance(move.narration, str) else "",
    }


class GeneratePdfWizard(models.TransientModel):
    _name = "pdfgen.generate.wizard"
    _description = "Generate PDF via pdfgeneratorapi.com"

    move_id = fields.Many2one(
        "account.move",
        string="Invoice",
        required=True,
        ondelete="cascade",
    )
    template_id = fields.Selection(
        selection="_selection_template_id",
        string="Template",
        required=True,
    )

    @api.model
    def _build_client(self):
        icp = self.env["ir.config_parameter"].sudo()
        key = icp.get_param("pdfgen.api_key")
        secret = icp.get_param("pdfgen.api_secret")
        workspace = icp.get_param("pdfgen.workspace_identifier")
        if not (key and secret and workspace):
            raise UserError(_(
                "PDF Generator API is not configured. Go to "
                "Settings > PDF Generator API."
            ))
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
            response = client.list_templates(per_page=200)
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

    def action_generate(self):
        self.ensure_one()
        if not self.template_id:
            raise UserError(_("Select a template first."))
        client = self._build_client()
        data = _serialize_invoice(self.move_id)
        filename = f"{(self.move_id.name or 'invoice').replace('/', '_')}.pdf"
        try:
            response = client.generate(
                template_id=self.template_id,
                data=data,
                name=filename,
                output="base64",
                fmt="pdf",
            )
        except PdfGenApiError as e:
            raise UserError(_(
                "PDF generation failed (HTTP %s): %s",
                e.status or "—",
                (e.body or "no body")[:500],
            ))

        pdf_b64 = self._extract_pdf_payload(response)
        if not pdf_b64:
            raise UserError(_(
                "Unexpected API response shape. Got keys: %s",
                list(response.keys()) if isinstance(response, dict) else type(response).__name__,
            ))

        try:
            base64.b64decode(pdf_b64, validate=True)
        except (ValueError, TypeError) as e:
            raise UserError(_("API returned invalid base64: %s", e))

        attachment = self.env["ir.attachment"].create({
            "name": filename,
            "type": "binary",
            "datas": pdf_b64,
            "res_model": "account.move",
            "res_id": self.move_id.id,
            "mimetype": "application/pdf",
        })
        self.move_id.message_post(
            body=_("Generated custom PDF via pdfgeneratorapi.com."),
            attachment_ids=[attachment.id],
        )
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    @staticmethod
    def _extract_pdf_payload(response):
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
