"""Wizard that embeds the pdfgeneratorapi.com template editor inside Odoo.

Users pick a template from the live list (or create a new one) and Odoo fetches
a short-lived signed editor URL via ``POST /templates/{id}/editor``, then writes
it onto the wizard's ``editor_url`` field. The OWL ``pdfgen_editor_iframe``
field widget picks up the value and sets its ``<iframe src>`` — so the editor
loads inline, below the selector, inside the same Odoo page (no modal).

The wizard is opened full-page (action target=current) so the iframe has room
to render and so cross-site cookie policies (which block third-party cookies
inside sandboxed / iframed contexts in some browsers) don't interfere.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..models.pdfgen_api_client import PdfGenApiError

_logger = logging.getLogger(__name__)


class PdfgenTemplateEditorWizard(models.TransientModel):
    _name = "pdfgen.template.editor.wizard"
    _description = "Embed the pdfgeneratorapi.com template editor"

    template_id = fields.Selection(
        selection="_selection_template_id",
    )
    new_template_name = fields.Char(
        string="New template name",
        default="New template",
        help="Used when clicking Create new template.",
    )
    editor_url = fields.Char(
        string="Editor URL",
        readonly=True,
        help=(
            "Short-lived signed URL rendered by the pdfgen_editor_iframe widget. "
            "Not cached between actions — each Open editor click mints a fresh one."
        ),
    )

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

    def action_open_editor(self):
        self.ensure_one()
        if not self.template_id:
            raise UserError(_("Pick a template first."))
        client = self._build_client()
        try:
            url = client.open_editor(self.template_id)
        except PdfGenApiError as e:
            raise UserError(
                _(
                    "Could not load the editor (HTTP %(status)s): %(body)s",
                    status=e.status or "—",
                    body=(e.body or "no body")[:500],
                )
            ) from e
        if not url:
            raise UserError(_("openEditor returned no URL."))
        self.editor_url = url
        # Return no action — Odoo re-reads the record automatically after a
        # button method; the OWL iframe widget's useEffect fires with the new
        # URL and swaps the <iframe src>. Keeps the user on the same page.
        return False

    def action_create_template(self):
        self.ensure_one()
        name = (self.new_template_name or "").strip() or _("New template")
        client = self._build_client()
        try:
            response = client.create_template(name)
        except PdfGenApiError as e:
            raise UserError(
                _(
                    "Could not create template (HTTP %(status)s): %(body)s",
                    status=e.status or "—",
                    body=(e.body or "no body")[:500],
                )
            ) from e
        template = response.get("response", response) if isinstance(response, dict) else response
        new_id = template.get("id") if isinstance(template, dict) else None
        if new_id is None:
            raise UserError(_("Template was created but the response had no id. Got: %s", template))
        # Re-open the editor on the freshly minted template so the user can
        # start designing immediately.
        self.template_id = str(new_id)
        return self.action_open_editor()
