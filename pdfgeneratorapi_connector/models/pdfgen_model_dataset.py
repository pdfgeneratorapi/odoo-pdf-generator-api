import logging
from typing import Self

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import pdfgen_resolver
from .pdfgen_document_mixin import build_pdfgen_client, pdfgen_template_selection

_logger = logging.getLogger(__name__)


class PdfGenModelDataset(models.Model):
    _name = "pdfgen.model.dataset"
    _description = "pdfgeneratorapi.com payload dataset for an Odoo model"
    _rec_name = "name"
    _order = "name"

    name = fields.Char(required=True)
    model_id = fields.Many2one(
        "ir.model",
        required=True,
        string="Odoo Model",
        ondelete="cascade",
        domain="[('transient', '=', False)]",
        help="Dataset applies to every PDF generated from this model.",
    )
    model = fields.Char(related="model_id.model", store=True, readonly=True)
    active = fields.Boolean(default=True)
    default_template_id = fields.Selection(
        selection="_selection_default_template_id",
        string="Default template",
        help=(
            "Template the Send wizard auto-generates a document with when a "
            "record has no PDF API document yet. Pick from your live "
            "pdfgeneratorapi.com templates. Leave blank to disable auto-"
            "generation for this model."
        ),
    )
    line_ids = fields.One2many(
        "pdfgen.model.dataset.line",
        "dataset_id",
        string="Field Mappings",
    )

    @api.model
    def _selection_default_template_id(self) -> list[tuple[str, str]]:
        # `build_pdfgen_client` is resolved through this module's namespace
        # at call time so tests can patch it here.
        return pdfgen_template_selection(self.env, lambda: build_pdfgen_client(self.env))

    # Odoo 19 ignores `_sql_constraints` outright (it only logs a warning), so
    # this has to be a models.Constraint or the uniqueness is silently dropped.
    # The attribute name and definition string deliberately match what the old
    # entry spelled: Odoo derives the constraint name from the attribute and
    # compares the definition against the string it stored as a Postgres
    # comment, so keeping both lets a database upgraded from 18.0 recognise its
    # existing constraint instead of rebuilding it.
    _unique_model_id = models.Constraint(
        "unique(model_id)",
        "A dataset for this model already exists.",
    )

    @api.model_create_multi
    def create(self, vals_list: list[dict]) -> Self:
        for vals in vals_list:
            if not vals.get("name") and vals.get("model_id"):
                model = self.env["ir.model"].browse(vals["model_id"])
                vals["name"] = model.name or model.model
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Wizard launchers (header buttons on the dataset form)
    # ------------------------------------------------------------------

    def _first_sample_record_id(self) -> int:
        """First record of the dataset's model, or 0 if none exist.

        Used as the auto-default for the template editor wizard's sample
        record picker. We don't filter for `posted` / `done` / similar
        state — the dataset only needs *some* record so the editor can
        render real data.
        """
        self.ensure_one()
        if not self.model or self.model not in self.env:
            return 0
        record = self.env[self.model].search([], limit=1)
        return record.id if record else 0

    def action_open_in_editor(self) -> dict:
        """Open the pdfgen.com template editor with this dataset prefilled.

        The template editor wizard pre-fills `dataset_id` (so the placeholder
        palette knows what model to walk) and `sample_record_id` (so the
        editor renders against real data). If the dataset has a
        `default_template_id`, that template is selected too — user just
        clicks `Open` to launch the editor iframe.
        """
        self.ensure_one()
        ctx = {"default_dataset_id": self.id}
        sample = self._first_sample_record_id()
        if sample:
            ctx["default_sample_record_id"] = sample
        if self.default_template_id:
            ctx["default_template_id"] = self.default_template_id
        return {
            "type": "ir.actions.act_window",
            "name": _("Template Editor"),
            "res_model": "pdfgen.template.editor.wizard",
            "view_mode": "form",
            "target": "current",
            "context": ctx,
        }

    def action_open_preview(self) -> dict:
        """Open the coverage/preview wizard. Auto-renders if a default
        template is configured so the user lands on the rendered HTML
        instead of an empty preview pane.

        Falls back to opening the wizard for manual template selection
        when `default_template_id` is empty.
        """
        self.ensure_one()
        if not self.default_template_id:
            return {
                "type": "ir.actions.act_window",
                "name": _("Preview"),
                "res_model": "pdfgen.coverage.wizard",
                "view_mode": "form",
                "target": "new",
                "context": {"default_dataset_id": self.id},
            }
        wizard = self.env["pdfgen.coverage.wizard"].create(
            {"dataset_id": self.id, "template_id": self.default_template_id}
        )
        # action_preview() returns a `_reopen()` action dict that lands the
        # user back on this wizard with `preview_html` populated.
        return wizard.action_preview()

    def resolve_payload(self, record: models.Model) -> dict:
        """Turn an Odoo record into the JSON payload every template receives."""
        self.ensure_one()
        if record._name != self.model:
            raise UserError(
                _(
                    "This dataset targets %(expected)s but the record is %(got)s.",
                    expected=self.model,
                    got=record._name,
                )
            )
        root_lines = [_LineView(line) for line in self.line_ids if not line.parent_id]
        return pdfgen_resolver.resolve(record, root_lines)


class PdfGenModelDatasetLine(models.Model):
    _name = "pdfgen.model.dataset.line"
    _description = "pdfgeneratorapi.com dataset placeholder mapping"
    _order = "sequence, id"

    dataset_id = fields.Many2one(
        "pdfgen.model.dataset",
        required=True,
        ondelete="cascade",
        index=True,
    )
    parent_id = fields.Many2one(
        "pdfgen.model.dataset.line",
        ondelete="cascade",
        index=True,
        help="For children of a list placeholder, the list line.",
    )
    child_ids = fields.One2many(
        "pdfgen.model.dataset.line",
        "parent_id",
    )

    sequence = fields.Integer(default=10)
    placeholder_path = fields.Char(
        required=True,
        help="Dotted path in the template JSON. Relative to the parent list for children.",
    )
    is_list = fields.Boolean(
        help="True when the placeholder is an array of items (repeated section).",
    )
    odoo_field_path = fields.Char(
        string="Odoo Field",
        help=(
            "Dotted Odoo attribute path from the dataset's model (or from the parent "
            "list's iterated record, for children). Ignored when Expression is set."
        ),
    )
    expression = fields.Char(
        help=(
            "Template string composing multiple fields. Use {dotted.path} tokens, "
            "e.g. '{street}, {city} {zip}'. When set, beats the Odoo Field column."
        ),
    )

    dataset_model = fields.Char(
        related="dataset_id.model",
        readonly=True,
        help="The root model of the dataset — same for every row.",
    )
    target_model = fields.Char(
        compute="_compute_target_model",
        help=(
            "Model the field picker should browse for this row. "
            "Matches the root model for top-level rows; for children of a list "
            "placeholder, walks the parent's path to the related model."
        ),
    )

    @api.depends("parent_id.odoo_field_path", "dataset_id.model")
    def _compute_target_model(self) -> None:
        for rec in self:
            root = rec.dataset_id.model or ""
            if not rec.parent_id:
                rec.target_model = root
                continue
            path = (rec.parent_id.odoo_field_path or "").strip()
            model = root
            for segment in filter(None, path.split(".")):
                field_def = self.env[model]._fields.get(segment) if model else None
                if not field_def or not getattr(field_def, "relational", False):
                    model = root
                    break
                model = field_def.comodel_name or root
            rec.target_model = model or root


class _LineView:
    """Adapter giving dataset-line records the duck-typed shape the resolver expects."""

    __slots__ = ("_line",)

    def __init__(self, line: models.Model) -> None:
        self._line = line

    @property
    def placeholder_path(self) -> str:
        return self._line.placeholder_path

    @property
    def odoo_field_path(self) -> str:
        return self._line.odoo_field_path or ""

    @property
    def expression(self) -> str:
        return self._line.expression or ""

    @property
    def is_list(self) -> bool:
        return self._line.is_list

    @property
    def child_lines(self) -> list["_LineView"]:
        return [_LineView(c) for c in self._line.child_ids]
