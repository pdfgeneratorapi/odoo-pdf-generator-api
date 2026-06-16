"""Inherits Odoo 17's `account.move.send` wizard to substitute the standard PDF
report with the latest pdfgen-generated PDF (or a freshly-generated one).

Odoo 17's Send & Print is a single multi-move wizard (`account.move.send`,
with `move_ids` + a `mode`); Odoo 18 split it into a per-move
`account.move.send.wizard` (`move_id`). pdfgen substitution is inherently
per-document, so we only act in `invoice_single` mode — `_pdfgen_move()`
returns the lone move and an empty recordset otherwise.

The wizard's view shows a `mail_attachments_widget` JSON list. The
"placeholder" entry — the standard report that Odoo renders at send time — is
what we substitute. When `pdfgen_use_custom` is on:

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
from odoo.addons.pdfgeneratorapi_connector.fields import TolerantSelection
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccountMoveSend(models.TransientModel):
    _name = "account.move.send"
    _inherit = ["account.move.send", "pdfgen.send.mixin"]

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
    pdfgen_template_id = TolerantSelection(
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

    def _pdfgen_move(self):
        """The single move our per-document substitution targets.

        Odoo 17's wizard can batch several invoices (`move_ids` + `mode`);
        pdfgen substitution only makes sense for one invoice, so return the
        lone move in `invoice_single` mode and an empty recordset otherwise.
        """
        self.ensure_one()
        # Use len(move_ids)==1 rather than mode=='invoice_single': they're
        # equivalent (mode is computed from move_ids) but `mode` may be unset
        # on a `.new()` wizard built directly in a test.
        return self.move_ids if len(self.move_ids) == 1 else self.env["account.move"]

    @api.depends("move_ids", "mode")
    def _compute_pdfgen_configured(self):
        for wiz in self:
            move = wiz._pdfgen_move()
            wiz.pdfgen_configured = bool(move) and bool(wiz._pdfgen_dataset(move))

    @api.depends("move_ids", "mode")
    def _compute_pdfgen_use_custom(self):
        for wiz in self:
            move = wiz._pdfgen_move()
            wiz.pdfgen_use_custom = bool(move) and wiz._pdfgen_should_default_on(move)

    @api.depends("move_ids", "mode", "pdfgen_use_custom")
    def _compute_pdfgen_template_id(self):
        for wiz in self:
            move = wiz._pdfgen_move()
            if wiz.pdfgen_use_custom and move:
                wiz.pdfgen_template_id = wiz._pdfgen_pick_template_id(move)
            else:
                wiz.pdfgen_template_id = False

    @api.depends("pdfgen_template_id", "pdfgen_use_custom")
    def _compute_pdfgen_preview_html(self):
        for wiz in self:
            move = wiz._pdfgen_move()
            if wiz.pdfgen_use_custom and wiz.pdfgen_template_id and move:
                wiz.pdfgen_preview_html = wiz._pdfgen_render_preview_html(
                    wiz.pdfgen_template_id, move
                )
            else:
                wiz.pdfgen_preview_html = False

    @api.model
    def _selection_pdfgen_template_id(self):
        # Same live-fetch the dataset's default template uses — share via the
        # dataset selection helper so we hit the API at most once per request.
        return self.env["pdfgen.model.dataset"]._selection_default_template_id()

    @api.depends("mail_template_id", "pdfgen_use_custom", "pdfgen_template_id")
    # pylint-odoo's missing-return rule fires because we use super(); for
    # compute methods Odoo expects no return value, so suppress.
    # pylint: disable=missing-return
    def _compute_mail_attachments_widget(self):
        # Odoo 17's base compute (re)builds the widget in invoice_single mode
        # off `mail_template_id`; defer to it first so manual / dynamic entries
        # are present, then post-process to swap the placeholder.
        super()._compute_mail_attachments_widget()
        for wiz in self:
            if not wiz.pdfgen_use_custom:
                continue
            move = wiz._pdfgen_move()
            if not move:
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
        """Make the pdfgen PDF the move's official report and reflect that
        in the wizard's attachment widget.

        Steps, in order so each one has a consistent view of state:

          1. Find / mint the pdfgen attachment for the chosen template.
          2. Point `move.invoice_pdf_report_id` at it — Odoo's `Document
             Preview` pane and `_get_invoice_extra_attachments` both read
             this field.
          3. Strip the placeholder entry (the to-be-generated standard
             report) and any pre-existing extra-attachment entries that
             aren't manual user uploads. Keep template / manual / dynamic
             entries untouched.
          4. Append the pdfgen attachment as the sole protected PDF.
        """
        self.ensure_one()
        if not self.pdfgen_template_id:
            raise UserError(_("Pick a template to use the pdfgen PDF."))
        # Pin the move recordset locally — `ir.attachment.create` below
        # invalidates wizard caches, and re-reading the wizard on a NewId
        # record would re-trigger default_get (and crash without active_ids
        # in context).
        move = self._pdfgen_move()
        latest = self._pdfgen_latest_pdfgen_attachment(move)
        if not latest or (
            self.pdfgen_template_id
            and latest.description
            and not latest.description.endswith(f":{self.pdfgen_template_id}")
        ):
            latest = self._pdfgen_generate_attachment(self.pdfgen_template_id, move)
        # 2. Transfer the `invoice_pdf_report_file` res_field claim from
        # the standard report to our pdfgen attachment so Odoo's
        # computed `invoice_pdf_report_id` resolves to ours and the
        # form-view preview pane reflects it.
        self._pdfgen_promote_attachment(move, latest)
        # 3. Strip placeholder + non-manual existing-PDF entries.
        out = []
        for w in widget:
            is_placeholder = w.get("placeholder") and not w.get("dynamic_report")
            is_existing_pdf = (
                not w.get("placeholder")
                and not w.get("manual")
                and w.get("mimetype") == "application/pdf"
                and w.get("protect_from_deletion")
            )
            if is_placeholder or is_existing_pdf:
                continue
            out.append(w)
        # 4. Inject the pdfgen attachment.
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
