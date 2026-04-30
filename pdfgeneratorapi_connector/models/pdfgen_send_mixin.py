"""Helpers for Send wizards that substitute the standard report PDF
with a pdfgen-generated one.

`pdfgen.send.mixin` is an `AbstractModel` that any Send wizard can
`_inherit` to gain:

- `_pdfgen_latest_pdfgen_attachment(record)` — newest pdfgen PDF on
  the record (description starts with "pdfgen:").
- `_pdfgen_latest_standard_attachment(record)` — newest non-pdfgen PDF
  on the record (the printed standard report).
- `_pdfgen_pick_template_id(record)` — resolution chain: template id
  parsed from the latest pdfgen attachment > dataset.default_template_id
  > False.
- `_pdfgen_should_default_on(record)` — True when latest-wins logic or a
  configured default template means the toggle should start ON.
- `_pdfgen_render_preview_html(template_id, record)` — calls
  `client.generate(..., fmt="html")`, returns sanitised HTML.
- `_pdfgen_generate_attachment(template_id, record)` — synchronous
  PDF generation. Creates the `ir.attachment` (with the `pdfgen:` marker
  the existing flows use) and returns it. Raises `UserError` on
  failure so the calling wizard can surface the message in the modal.

The concrete Send wizards (one per model — only `account.move` ships
today, the bridge wizards land in a follow-up commit) just expose the
fields and call these helpers from their attachment-collection hooks.
"""

import base64
import logging
import re

from odoo import _, models
from odoo.exceptions import UserError

from .pdfgen_api_client import PdfGenApiError
from .pdfgen_document_mixin import build_pdfgen_client

_logger = logging.getLogger(__name__)

_TEMPLATE_RE = re.compile(r"^pdfgen:template:(?P<tid>[^:\s]+)")


class PdfgenSendMixin(models.AbstractModel):
    _name = "pdfgen.send.mixin"
    _description = "Helpers for Send wizards that substitute pdfgen PDFs"

    # ---------------------------------------------------------------- look-up

    def _pdfgen_dataset(self, record):
        if not record:
            return self.env["pdfgen.model.dataset"]
        return self.env["pdfgen.model.dataset"].search(
            [("model", "=", record._name), ("active", "=", True)],
            limit=1,
        )

    def _pdfgen_latest_pdfgen_attachment(self, record):
        if not record:
            return self.env["ir.attachment"]
        return self.env["ir.attachment"].search(
            [
                ("res_model", "=", record._name),
                ("res_id", "=", record.id),
                ("description", "=like", "pdfgen:%"),
                ("mimetype", "=", "application/pdf"),
            ],
            order="create_date desc, id desc",
            limit=1,
        )

    def _pdfgen_latest_standard_attachment(self, record):
        if not record:
            return self.env["ir.attachment"]
        return self.env["ir.attachment"].search(
            [
                ("res_model", "=", record._name),
                ("res_id", "=", record.id),
                ("mimetype", "=", "application/pdf"),
                "|",
                ("description", "=", False),
                ("description", "not like", "pdfgen:%"),
            ],
            order="create_date desc, id desc",
            limit=1,
        )

    def _pdfgen_pick_template_id(self, record):
        """Resolution: template parsed from the latest pdfgen attachment >
        dataset.default_template_id > False.
        """
        latest = self._pdfgen_latest_pdfgen_attachment(record)
        if latest and latest.description:
            match = _TEMPLATE_RE.match(latest.description)
            if match:
                return match.group("tid")
        dataset = self._pdfgen_dataset(record)
        return dataset.default_template_id or False

    def _pdfgen_should_default_on(self, record):
        """True iff the toggle should start ON for this record.

        - pdfgen attachment exists AND is newer than the standard report.
        - OR no pdfgen attachment yet but the dataset has a default template.
        """
        if not record:
            return False
        pdfgen_att = self._pdfgen_latest_pdfgen_attachment(record)
        standard_att = self._pdfgen_latest_standard_attachment(record)
        if pdfgen_att:
            if not standard_att:
                return True
            return pdfgen_att.create_date >= standard_att.create_date
        return bool(self._pdfgen_dataset(record).default_template_id)

    # -------------------------------------------------------- preview / render

    def _pdfgen_render_preview_html(self, template_id, record):
        """Render the chosen template against the record as HTML for the
        modal preview. Empty string on any failure — preview is best-effort
        and shouldn't block the user from sending.
        """
        if not (template_id and record):
            return ""
        dataset = self._pdfgen_dataset(record)
        if not dataset:
            return ""
        try:
            client = build_pdfgen_client(self.env)
            data = dataset.resolve_payload(record)
            response = client.generate(
                template_id=template_id,
                data=data,
                name=f"preview-{record._name}-{record.id}.html",
                output="base64",
                fmt="html",
            )
        except (PdfGenApiError, UserError) as e:
            _logger.warning("pdfgen preview failed for %s(%s): %s", record._name, record.id, e)
            return ""
        b64 = self._pdfgen_extract_payload(response)
        if not b64:
            return ""
        try:
            return base64.b64decode(b64).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            return ""

    def _pdfgen_generate_attachment(self, template_id, record):
        """Synchronous PDF generation. Creates an `ir.attachment` on the
        record and returns it. Raises `UserError` on failure.
        """
        if not (template_id and record):
            raise UserError(_("Pick a template before generating."))
        dataset = self._pdfgen_dataset(record)
        if not dataset:
            raise UserError(
                _(
                    "No active dataset found for %s. "
                    "Create one under PDF Generator API > Field Datasets.",
                    record._name,
                )
            )
        client = build_pdfgen_client(self.env)
        data = dataset.resolve_payload(record)
        stem = (record.display_name or record._name).replace("/", "_")
        filename = f"{stem}.pdf"
        try:
            response = client.generate(
                template_id=template_id,
                data=data,
                name=filename,
                output="base64",
                fmt="pdf",
            )
        except PdfGenApiError as e:
            raise UserError(
                _(
                    "PDF generation failed (HTTP %(status)s): %(body)s",
                    status=e.status or "—",
                    body=(e.body or "no body")[:500],
                )
            ) from e
        pdf_b64 = self._pdfgen_extract_payload(response)
        if not pdf_b64:
            raise UserError(_("Unexpected API response shape from pdfgeneratorapi.com."))
        try:
            base64.b64decode(pdf_b64, validate=True)
        except (ValueError, TypeError) as e:
            raise UserError(_("API returned invalid base64: %s", e)) from e

        # Honour the same Replace/Keep cleanup policy the sync wizard uses,
        # so the two flows interoperate.
        icp = self.env["ir.config_parameter"].sudo()
        if icp.get_param("pdfgen.attachment_cleanup") == "replace":
            self.env["ir.attachment"].search(
                [
                    ("res_model", "=", record._name),
                    ("res_id", "=", record.id),
                    ("description", "=like", "pdfgen:%"),
                ]
            ).unlink()
        return self.env["ir.attachment"].create(
            {
                "name": filename,
                "type": "binary",
                "datas": pdf_b64,
                "res_model": record._name,
                "res_id": record.id,
                "mimetype": "application/pdf",
                "description": f"pdfgen:template:{template_id}",
            }
        )

    @staticmethod
    def _pdfgen_extract_payload(response):
        """Pull the base64 payload out of pdfgen's response envelope —
        same shape-tolerant matcher the generate wizard uses.
        """
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
