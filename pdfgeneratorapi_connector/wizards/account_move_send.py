"""Inherits `account.move.send.wizard` to substitute the standard PDF report
with the latest pdfgen-generated PDF (or a freshly-generated one).

The wizard's stock view shows a `mail_attachments_widget` JSON list. The
"placeholder" entry — the standard report that pdfgen Odoo will render at
send time — is what we substitute. When `pdfgen_use_custom` is on:

  - if a recent pdfgen attachment already exists on the move, we drop the
    placeholder and add the existing attachment as a regular entry;
  - else we generate the PDF synchronously via the mixin, then add the
    fresh attachment to the widget.

Failure is surfaced via `pdfgen_error` (read-only Char shown in the modal)
and the toggle force-resets to OFF so the user can fall back to the
standard report.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccountMoveSendWizard(models.TransientModel):
    _name = "account.move.send.wizard"
    _inherit = ["account.move.send.wizard", "pdfgen.send.mixin"]

    pdfgen_configured = fields.Boolean(compute="_compute_pdfgen_configured")
    pdfgen_use_custom = fields.Boolean(
        string="Use pdfgen PDF",
        compute="_compute_pdfgen_use_custom",
        store=False,
        readonly=False,
        help=(
            "Replace the standard invoice report with the latest pdfgen-"
            "generated PDF (or generate one synchronously from the dataset's "
            "default template)."
        ),
    )
    pdfgen_template_id = fields.Selection(
        selection="_selection_pdfgen_template_id",
        string="pdfgen Template",
        compute="_compute_pdfgen_template_id",
        store=False,
        readonly=False,
    )
    pdfgen_preview_html = fields.Html(
        string="Preview",
        sanitize=False,
        readonly=True,
        compute="_compute_pdfgen_preview_html",
    )
    pdfgen_error = fields.Char(readonly=True)

    @api.depends("move_id")
    def _compute_pdfgen_configured(self):
        for wiz in self:
            wiz.pdfgen_configured = bool(wiz.move_id) and bool(wiz._pdfgen_dataset(wiz.move_id))

    @api.depends("move_id")
    def _compute_pdfgen_use_custom(self):
        for wiz in self:
            wiz.pdfgen_use_custom = wiz._pdfgen_should_default_on(wiz.move_id)

    @api.depends("move_id", "pdfgen_use_custom")
    def _compute_pdfgen_template_id(self):
        for wiz in self:
            if wiz.pdfgen_use_custom:
                wiz.pdfgen_template_id = wiz._pdfgen_pick_template_id(wiz.move_id)
            else:
                wiz.pdfgen_template_id = False

    @api.depends("pdfgen_template_id", "pdfgen_use_custom")
    def _compute_pdfgen_preview_html(self):
        for wiz in self:
            if wiz.pdfgen_use_custom and wiz.pdfgen_template_id:
                wiz.pdfgen_preview_html = wiz._pdfgen_render_preview_html(
                    wiz.pdfgen_template_id, wiz.move_id
                )
            else:
                wiz.pdfgen_preview_html = False

    @api.model
    def _selection_pdfgen_template_id(self):
        # Same live-fetch the dataset's default template uses — share via the
        # dataset selection helper so we hit the API at most once per request.
        return self.env["pdfgen.model.dataset"]._selection_default_template_id()

    @api.depends(
        "template_id",
        "invoice_edi_format",
        "extra_edis",
        "pdf_report_id",
        "pdfgen_use_custom",
        "pdfgen_template_id",
    )
    # pylint-odoo's missing-return rule fires because we use super(); for
    # compute methods Odoo expects no return value, so suppress.
    # pylint: disable=missing-return
    def _compute_mail_attachments_widget(self):
        # Defer to the upstream compute first so manual / dynamic / extra
        # entries are present, then post-process to swap the placeholder.
        super()._compute_mail_attachments_widget()
        for wiz in self:
            if not wiz.pdfgen_use_custom:
                continue
            try:
                widget = wiz._pdfgen_apply_substitution(wiz.mail_attachments_widget or [])
            except UserError as e:
                wiz.pdfgen_error = str(e)
                wiz.pdfgen_use_custom = False
                continue
            wiz.mail_attachments_widget = widget
            wiz.pdfgen_error = False

    def _pdfgen_apply_substitution(self, widget):
        """Drop the standard report placeholder and inject the pdfgen
        attachment (existing latest, or freshly generated)."""
        self.ensure_one()
        if not self.pdfgen_template_id:
            raise UserError(_("Pick a template to use the pdfgen PDF."))
        latest = self._pdfgen_latest_pdfgen_attachment(self.move_id)
        if not latest or (
            self.pdfgen_template_id
            and latest.description
            and not latest.description.endswith(f":{self.pdfgen_template_id}")
        ):
            # Either no pdfgen attachment yet, or it was rendered with a
            # different template — generate a fresh one now.
            latest = self._pdfgen_generate_attachment(self.pdfgen_template_id, self.move_id)
        # Strip the placeholder PDF entry (the standard report Odoo would
        # otherwise render at send time) — leave manual / extra / dynamic
        # entries untouched.
        out = [w for w in widget if not (w.get("placeholder") and not w.get("dynamic_report"))]
        out.append(
            {
                "id": latest.id,
                "name": latest.name,
                "mimetype": "application/pdf",
                "placeholder": False,
                "protect_from_deletion": True,
            }
        )
        return out
