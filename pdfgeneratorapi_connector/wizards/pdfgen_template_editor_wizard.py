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

from ..models.pdfgen_api_client import LIBRARY_TEMPLATE_PREFIX, PdfGenApiClient, PdfGenApiError

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
    dataset_id = fields.Many2one(
        "pdfgen.model.dataset",
        domain=[("active", "=", True)],
        help=(
            "When set together with a Sample record, the resolved Odoo data is "
            "sent to the editor as preview data so you can design against your "
            "real records."
        ),
    )
    sample_model = fields.Char(related="dataset_id.model", readonly=True)
    sample_record_id = fields.Many2oneReference(
        string="Sample record",
        model_field="sample_model",
        help=(
            "Pick a record of the dataset's model. Its resolved payload is sent "
            "to the editor's preview pane so the template renders against real data."
        ),
    )
    editor_url = fields.Char(
        string="Editor URL",
        readonly=True,
        help=(
            "Short-lived signed URL rendered by the pdfgen_editor_iframe widget. "
            "Not cached between actions — each Open editor click mints a fresh one."
        ),
    )
    is_library_template = fields.Boolean(
        compute="_compute_is_library_template",
        help=(
            "True when the picked template comes from the public Template "
            "Library. Drives the 'a copy will be created' hint — library "
            "templates are read-only, so Open copies them into the account "
            "first."
        ),
    )

    # Magic selection value meaning "I want to create a new template instead
    # of editing an existing one." Picked up by `action_open_editor` and
    # rerouted to `action_create_template`.
    NEW_TEMPLATE_VALUE = "__new__"

    @api.onchange("dataset_id")
    def _onchange_dataset_id(self) -> None:
        # Auto-pick the first record of the dataset's model so the editor
        # renders against real data without the user having to dig out a
        # specific record. Falls back cleanly when the dataset's model has
        # zero records or isn't accessible — `_resolve_sample_data` then
        # returns None and the editor uses pdfgen's own dummy payload.
        if not self.dataset_id:
            self.sample_record_id = False
            return
        self.sample_record_id = self.dataset_id._first_sample_record_id() or False

    @api.depends("template_id")
    def _compute_is_library_template(self) -> None:
        for rec in self:
            rec.is_library_template = bool(
                rec.template_id and rec.template_id.startswith(LIBRARY_TEMPLATE_PREFIX)
            )

    @api.onchange("template_id")
    def _onchange_template_id(self) -> None:
        # Keep `new_template_name` reset until the user actually picks the
        # magic "+ Create new template" entry — that way switching between
        # existing templates doesn't strand a half-typed name in the form.
        if self.template_id != self.NEW_TEMPLATE_VALUE:
            self.new_template_name = False

    def _compute_display_name(self) -> None:
        """Force a friendly breadcrumb label.

        TransientModels with no `name` field fall back to
        `<model>,NewId_<n>` in the breadcrumb (and in the browser tab),
        which leaks the technical model name to users. This wizard is
        always a singleton from the user's perspective, so a constant
        label is correct.
        """
        for rec in self:
            rec.display_name = "Template Editor"

    @api.model
    def _build_client(self) -> PdfGenApiClient:
        from ..models.pdfgen_document_mixin import build_pdfgen_client

        return build_pdfgen_client(self.env)

    @api.model
    def _selection_template_id(self) -> list[tuple[str, str]]:
        # When the API is unreachable / unconfigured the shared builder
        # returns an empty list: the user has no way to create a template
        # without working creds, so showing the "+ Create new template"
        # affordance would just bait them into a 401. Once the list call
        # succeeds — even if it returns zero templates — the magic entry is
        # prepended so a fresh workspace can mint its first template
        # directly from the dropdown. Library ("Default") templates follow,
        # then the account's own templates.
        from ..models.pdfgen_document_mixin import pdfgen_template_selection

        return pdfgen_template_selection(self.env, self._build_client, include_create=True)

    def _resolve_sample_data(self) -> dict | None:
        """Build the openEditor `data` payload from the picked dataset+record.

        Returns None when either is missing or the record can't be resolved —
        the editor then falls back to pdfgenapi.com's own dummy preview data.
        """
        self.ensure_one()
        if not (self.dataset_id and self.sample_record_id and self.sample_model):
            return None
        if self.sample_model not in self.env:
            return None
        record = self.env[self.sample_model].browse(self.sample_record_id).exists()
        if not record:
            return None
        try:
            return self.dataset_id.resolve_payload(record)
        except Exception as e:
            _logger.warning(
                "template editor: resolve_payload failed for %s(%s): %s",
                self.sample_model,
                self.sample_record_id,
                e,
            )
            return None

    def action_open_editor(self) -> bool:
        self.ensure_one()
        if not self.template_id:
            raise UserError(_("Pick a template first."))
        if self.template_id == self.NEW_TEMPLATE_VALUE:
            # Magic dropdown entry — branch to the creation path which mints
            # a new template, swaps the dropdown's value to the real id, then
            # calls back into us for the actual editor URL.
            if not (self.new_template_name or "").strip():
                raise UserError(_("Type a name for the new template."))
            return self.action_create_template()
        if self.template_id.startswith(LIBRARY_TEMPLATE_PREFIX):
            # Library templates are read-only — copy the definition into the
            # account first, then reopen on the editable copy.
            return self.action_copy_library_template()
        client = self._build_client()
        try:
            url = client.open_editor(self.template_id, data=self._resolve_sample_data())
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

    def action_open_sample_record(self) -> dict:
        """Open the picked sample record in a side dialog so the user can sanity-check
        which record's data is feeding the editor preview.
        """
        self.ensure_one()
        if not (self.sample_model and self.sample_record_id):
            raise UserError(_("Pick a sample record first."))
        if self.sample_model not in self.env:
            raise UserError(_("Model %s is not available.", self.sample_model))
        return {
            "type": "ir.actions.act_window",
            "res_model": self.sample_model,
            "res_id": self.sample_record_id,
            "view_mode": "form",
            "target": "new",
        }

    def action_create_template(self) -> bool:
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

    def action_copy_library_template(self) -> bool:
        """Copy a public Template Library template into the account and open it.

        Library templates can't be edited in place — there's no editor
        endpoint for them. So: fetch the full definition, POST it to
        `/templates` (which accepts a TemplateDefinitionNew body), swap the
        dropdown to the new account template and recurse into
        `action_open_editor` — the same pattern `action_create_template`
        uses for blank templates.
        """
        self.ensure_one()
        public_id = self.template_id[len(LIBRARY_TEMPLATE_PREFIX) :]
        client = self._build_client()
        try:
            response = client.get_library_template(public_id)
        except PdfGenApiError as e:
            raise UserError(
                _(
                    "Could not load the default template (HTTP %(status)s): %(body)s",
                    status=e.status or "—",
                    body=(e.body or "no body")[:500],
                )
            ) from e
        definition = response.get("response", response) if isinstance(response, dict) else response
        if not isinstance(definition, dict):
            raise UserError(
                _("Unexpected template definition response. Got: %s", type(definition).__name__)
            )
        try:
            created = client.create_template(definition=definition)
        except PdfGenApiError as e:
            raise UserError(
                _(
                    "Could not copy the template to your account (HTTP %(status)s): %(body)s",
                    status=e.status or "—",
                    body=(e.body or "no body")[:500],
                )
            ) from e
        template = created.get("response", created) if isinstance(created, dict) else created
        new_id = template.get("id") if isinstance(template, dict) else None
        if new_id is None:
            raise UserError(_("Template was copied but the response had no id. Got: %s", template))
        self.template_id = str(new_id)
        return self.action_open_editor()
