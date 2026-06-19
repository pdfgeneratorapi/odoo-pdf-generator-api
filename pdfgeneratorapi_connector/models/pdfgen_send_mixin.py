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
  `client.generate(..., format=Format.HTML)`, returns sanitised HTML.
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

from ..enums import Format, Output
from .pdfgen_api_client import ApiResponse, PdfGenApiError
from .pdfgen_document_mixin import build_pdfgen_client

_logger = logging.getLogger(__name__)

# `\S+` (not `[^:\s]+`): library template ids are stored as `lib:<publicId>`,
# so the id itself contains a colon.
_TEMPLATE_RE = re.compile(r"^pdfgen:template:(?P<tid>\S+)")


class PdfgenSendMixin(models.AbstractModel):
    _name = "pdfgen.send.mixin"
    _description = "Helpers for Send wizards that substitute pdfgen PDFs"

    # ---------------------------------------------------------------- look-up

    def _pdfgen_dataset(self, record: models.Model) -> models.Model:
        if not record:
            return self.env["pdfgen.model.dataset"]
        return self.env["pdfgen.model.dataset"].search(
            [("model", "=", record._name), ("active", "=", True)],
            limit=1,
        )

    def _pdfgen_latest_pdfgen_attachment(self, record: models.Model) -> models.Model:
        if not record:
            return self.env["ir.attachment"]
        # The trailing res_field clause matches everything but suppresses
        # ir.attachment._search's implicit `res_field=False` filter — we
        # promote our attachment via res_field for canonical-PDF binding
        # and still need to find it here.
        return self.env["ir.attachment"].search(
            [
                ("res_model", "=", record._name),
                ("res_id", "=", record.id),
                ("description", "=like", "pdfgen:%"),
                ("mimetype", "=", "application/pdf"),
                "|",
                ("res_field", "=", False),
                ("res_field", "!=", False),
            ],
            order="create_date desc, id desc",
            limit=1,
        )

    def _pdfgen_latest_standard_attachment(self, record: models.Model) -> models.Model:
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
                "|",
                ("res_field", "=", False),
                ("res_field", "!=", False),
            ],
            order="create_date desc, id desc",
            limit=1,
        )

    def _pdfgen_pick_template_id(self, record: models.Model) -> str | bool:
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

    def _pdfgen_should_default_on(self, record: models.Model) -> bool:
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

    def _pdfgen_render_preview_html(self, template_id: str, record: models.Model) -> str:
        """Build the modal preview body.

        Strategy: if a pdfgen attachment for this template already exists on
        the record, embed the actual PDF in an iframe — that's faster than
        re-rendering, and shows exactly what the email recipient will get.
        Otherwise call the API for an HTML preview rendered against the
        chosen template + dataset payload.

        Empty string on any failure so the modal stays usable.
        """
        if not (template_id and record):
            return ""
        latest = self._pdfgen_latest_pdfgen_attachment(record)
        if latest and latest.description and latest.description.endswith(f":{template_id}"):
            return (
                f'<iframe src="/web/content/{latest.id}?download=false" '
                f'style="width:100%; min-height:500px; border:0;" '
                f'title="pdfgen preview"></iframe>'
            )
        return self._pdfgen_render_preview_html_via_api(template_id, record)

    def _pdfgen_render_preview_html_via_api(self, template_id: str, record: models.Model) -> str:
        """API-rendered HTML fallback for the preview modal — used when no
        live pdfgen attachment matches the chosen template. Empty string
        on any failure.
        """
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
                output=Output.BASE64,
                format=Format.HTML,
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

    def _pdfgen_generate_attachment(self, template_id: str, record: models.Model) -> models.Model:
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
        pdf_b64 = self._pdfgen_extract_payload(response)
        if not pdf_b64:
            raise UserError(_("Unexpected API response shape from pdfgeneratorapi.com."))
        try:
            base64.b64decode(pdf_b64, validate=True)
        except (ValueError, TypeError) as e:
            raise UserError(_("API returned invalid base64: %s", e)) from e

        # Honour the same Replace/Keep cleanup policy the sync wizard uses,
        # so the two flows interoperate. Default to `replace` when the param
        # is unset so fresh installs match the field's default.
        icp = self.env["ir.config_parameter"].sudo()
        if icp.get_param("pdfgen.attachment_cleanup", "replace") == "replace":
            self.env["ir.attachment"].search(
                [
                    ("res_model", "=", record._name),
                    ("res_id", "=", record.id),
                    ("description", "=like", "pdfgen:%"),
                    # Bypass ir.attachment._search's implicit res_field=False
                    # filter — Send promotes attachments via res_field, and
                    # cleanup must still find them.
                    "|",
                    ("res_field", "=", False),
                    ("res_field", "!=", False),
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

    def _pdfgen_promote_attachment(self, record: models.Model, attachment: models.Model) -> None:
        """Make `attachment` the record's canonical PDF attachment.

        Odoo binds the form-view "Document Preview" pane and the Send
        wizard's `_get_invoice_extra_attachments` to a computed Many2one
        (`invoice_pdf_report_id` on account.move) that resolves through
        an attachment whose `res_field` matches the related Binary field
        (`invoice_pdf_report_file`). Writing to the Many2one directly is
        a no-op — there's no inverse. Instead we transfer the `res_field`
        claim from the existing standard-report attachment to ours, and
        invalidate the cache so the computed Many2one re-resolves.

        The previous standard-report attachment is kept (just unbound from
        the binary-field claim) so the user retains an audit trail.
        """
        if not (record and attachment):
            return
        binary_field = self._pdfgen_canonical_binary_field(record)
        if not binary_field:
            return
        Att = self.env["ir.attachment"].sudo()
        existing = Att.search(
            [
                ("res_model", "=", record._name),
                ("res_id", "=", record.id),
                ("res_field", "=", binary_field),
                ("id", "!=", attachment.id),
            ]
        )
        if existing:
            existing.write({"res_field": False})
        if attachment.res_field != binary_field:
            attachment.sudo().write({"res_field": binary_field})
        # Bust the cache so the computed Many2one (invoice_pdf_report_id)
        # picks up our attachment on the next read.
        record.invalidate_recordset()

    @staticmethod
    def _pdfgen_canonical_binary_field(record: models.Model) -> str | None:
        """Name of the Binary field whose attachment becomes the model's
        canonical PDF (drives Odoo's preview pane + extras collection).

        Override per model to extend support beyond account.move.
        """
        if "invoice_pdf_report_file" in record._fields:
            return "invoice_pdf_report_file"
        return None

    @staticmethod
    def _pdfgen_extract_payload(response: ApiResponse) -> str | None:
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
