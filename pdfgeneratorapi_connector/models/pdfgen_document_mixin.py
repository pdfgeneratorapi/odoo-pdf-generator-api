"""Abstract mixin that any document model can inherit to expose the
`Generate custom PDF` button + wizard flow. Bridge modules (e.g.
pdfgeneratorapi_connector_sale) just `_inherit` the mixin on the target
model and add a view to surface the button.

Also hosts the shared pdfgen config-read helper used by every wizard —
per-company value if set, else global `ir.config_parameter` fallback.
"""

import logging
from collections.abc import Callable

from odoo import _, api, fields, models, release
from odoo.exceptions import UserError

from .pdfgen_api_client import (
    DEFAULT_BASE_URL,
    LIBRARY_TEMPLATE_PREFIX,
    PdfGenApiClient,
    PdfGenApiError,
)

_logger = logging.getLogger(__name__)


def pdfgen_config(env: api.Environment, key: str) -> str | None:
    """Return the effective pdfgen config value for the current company.

    Resolution order:
      1. `res.company.pdfgen_<key>` on `env.company` if set (per-company
         override, added in Phase "multi-company").
      2. `ir.config_parameter` `pdfgen.<key>` — global fallback, the
         pre-multi-company behaviour.
      3. `None` when neither has a value.
    """
    company = env.company
    value = getattr(company, f"pdfgen_{key}", None) if company else None
    if value:
        return value
    return env["ir.config_parameter"].sudo().get_param(f"pdfgen.{key}") or None


def build_pdfgen_client(env: api.Environment) -> PdfGenApiClient:
    """Shared client factory used by every wizard — reads pdfgen_config
    for creds and raises a translatable UserError if anything's missing."""
    key = pdfgen_config(env, "api_key")
    secret = pdfgen_config(env, "api_secret")
    workspace = pdfgen_config(env, "workspace_identifier")
    if not (key and secret and workspace):
        raise UserError(
            _("PDF Generator API is not configured. Go to Settings > PDF Generator API.")
        )
    return PdfGenApiClient(
        base_url=pdfgen_config(env, "api_base_url") or DEFAULT_BASE_URL,
        api_key=key,
        api_secret=secret,
        workspace_identifier=workspace,
        editor_web_url=pdfgen_config(env, "editor_web_url") or None,
        partner_id=f"odoo_v{release.version_info[0]}",
    )


def pdfgen_template_selection(
    env: api.Environment,
    build_client: Callable[[], PdfGenApiClient],
    include_create: bool = False,
    include_library: bool = True,
) -> list[tuple[str, str]]:
    """Build the Selection entries shared by every template dropdown.

    `build_client` is a zero-arg callable returning a PdfGenApiClient —
    passed in (rather than calling `build_pdfgen_client` here) so each
    caller keeps its own mockable factory hook (`self._build_client` on the
    wizards, the module-level `build_pdfgen_client` on the dataset).

    Order: optional "+ Create new template…" magic entry, then the public
    Template Library ("Default Templates", values `lib:<publicId>`), then
    the account's own templates ("My Templates", values `str(id)`). The
    `pdfgen_template_selection` JS widget groups the dropdown by these
    value shapes.

    Gating mirrors the historical behaviour: no working client or a failed
    own-templates fetch → empty list (a user who can't list their templates
    can't act on any of them). The library section is purely additive — any
    failure there just drops the section.
    """
    try:
        client = build_client()
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
    if include_create:
        result.append(("__new__", _("+ Create new template…")))
    if include_library:
        try:
            lib_response = client.list_library_templates()
        except Exception as e:
            _logger.warning("list_library_templates failed: %s", e)
            lib_response = None
        lib_templates = (
            lib_response.get("response") if isinstance(lib_response, dict) else lib_response
        )
        if isinstance(lib_templates, list):
            for t in lib_templates:
                if not isinstance(t, dict):
                    continue
                tid = t.get("id")
                if not tid:
                    continue
                name = t.get("name") or f"Template {tid}"
                result.append((f"{LIBRARY_TEMPLATE_PREFIX}{tid}", name))
    for t in templates:
        tid = t.get("id")
        if tid is None:
            continue
        name = t.get("name") or f"Template {tid}"
        result.append((str(tid), name))
    return result


class PdfgenDocumentMixin(models.AbstractModel):
    _name = "pdfgen.document.mixin"
    _description = "Expose the PDF Generator wizard on a document model"

    pdfgen_configured = fields.Boolean(
        compute="_compute_pdfgen_configured",
        help="True when PDF Generator API credentials are present.",
    )

    @api.depends_context("uid", "allowed_company_ids")
    def _compute_pdfgen_configured(self) -> None:
        ready = bool(
            pdfgen_config(self.env, "api_key")
            and pdfgen_config(self.env, "api_secret")
            and pdfgen_config(self.env, "workspace_identifier")
        )
        for record in self:
            record.pdfgen_configured = ready

    def action_open_pdfgen_wizard(self) -> dict:
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Generate custom PDF"),
            "res_model": "pdfgen.generate.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_res_model": self._name,
                "default_res_id": self.id,
            },
        }

    def action_view_pdfgen_async_jobs_from_list(self) -> dict:
        """Open the Async Jobs list filtered to the selected records.

        When invoked from a list-view header button the recordset is the
        current selection, so we scope the jobs view to those rows. With
        no selection (form-view button or programmatic call on an empty
        recordset) we fall back to all jobs for the model.
        """
        domain = [("res_model", "=", self._name)]
        if self.ids:
            domain.append(("res_id", "in", self.ids))
        return {
            "type": "ir.actions.act_window",
            "name": _("Async PDF Jobs"),
            "res_model": "pdfgen.async.job",
            "view_mode": "list,form",
            "domain": domain,
            "target": "current",
        }

    def action_open_pdfgen_wizard_from_list(self) -> dict:
        """Entry point for the list-view header button.

        Single record → opens the existing sync wizard.
        Multiple records → opens the async dispatch wizard which fans out
        one `/documents/generate/async` call per record and tracks them in
        `pdfgen.async.job`.
        """
        if not self:
            raise UserError(_("Select at least one record."))
        if len(self) == 1:
            return self.action_open_pdfgen_wizard()
        return {
            "type": "ir.actions.act_window",
            "name": _("Generate custom PDFs"),
            "res_model": "pdfgen.async.dispatch.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "active_model": self._name,
                "active_ids": self.ids,
            },
        }
