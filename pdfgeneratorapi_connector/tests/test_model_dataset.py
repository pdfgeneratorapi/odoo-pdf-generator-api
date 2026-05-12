"""Generic tests for the `pdfgen.model.dataset` model.

The account-coupled assertions (seed-XML structure, payload resolution on
an invoice, dataset-line `target_model` walks) live in the invoicing
bridge — those depend on this addon's sibling `pdfgeneratorapi_connector_account`.
What stays here is the framework-only surface: dataset creation defaults,
expression-vs-path precedence, the `target_model` fall-back, and the
`_selection_default_template_id` swallowing rules. All exercised against
`res.partner`, which is always available from `base`.
"""

from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestModelDataset(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner_model = cls.env.ref("base.model_res_partner")

    def _new_dataset(self):
        return self.env["pdfgen.model.dataset"].create(
            {
                "name": "Partner dataset",
                "model_id": self.partner_model.id,
            }
        )

    def test_expression_beats_odoo_field_path(self):
        dataset = self._new_dataset()
        line = self.env["pdfgen.model.dataset.line"].create(
            {
                "dataset_id": dataset.id,
                "placeholder_path": "display",
                "odoo_field_path": "name",
                "expression": "Name: {name}",
            }
        )
        partner = self.env["res.partner"].create({"name": "Acme"})
        payload = dataset.resolve_payload(partner)
        self.assertEqual(payload["display"], "Name: Acme")
        # Removing the expression falls back to the bare path.
        line.expression = False
        payload = dataset.resolve_payload(partner)
        self.assertEqual(payload["display"], "Acme")

    def test_name_defaults_from_model_on_create_when_blank(self):
        dataset = self.env["pdfgen.model.dataset"].create({"model_id": self.partner_model.id})
        self.assertTrue(dataset.name)

    def test_target_model_falls_back_when_parent_path_invalid(self):
        dataset = self.env["pdfgen.model.dataset"].create(
            {"name": "X", "model_id": self.partner_model.id}
        )
        parent = self.env["pdfgen.model.dataset.line"].create(
            {
                "dataset_id": dataset.id,
                "placeholder_path": "items",
                "is_list": True,
                "odoo_field_path": "not_a_real_field.more",
            }
        )
        child = self.env["pdfgen.model.dataset.line"].create(
            {
                "dataset_id": dataset.id,
                "parent_id": parent.id,
                "placeholder_path": "x",
            }
        )
        # Invalid parent path → target_model falls back to the dataset's root model.
        self.assertEqual(child.target_model, "res.partner")

    def test_selection_default_template_returns_live_list(self):
        client = MagicMock()
        client.list_templates.return_value = {
            "response": [{"id": 1, "name": "Invoice"}, {"id": 2, "name": "Quote"}]
        }
        with patch(
            "odoo.addons.pdfgeneratorapi_connector.models.pdfgen_model_dataset.build_pdfgen_client",
            return_value=client,
        ):
            sel = self.env["pdfgen.model.dataset"]._selection_default_template_id()
        self.assertEqual(sel, [("1", "Invoice"), ("2", "Quote")])

    def test_selection_default_template_swallows_unconfigured(self):
        # Wipe creds → build_pdfgen_client raises UserError → empty list.
        icp = self.env["ir.config_parameter"].sudo()
        for key in ("pdfgen.api_key", "pdfgen.api_secret", "pdfgen.workspace_identifier"):
            icp.set_param(key, "")
        self.env.company.write(
            {
                "pdfgen_api_key": False,
                "pdfgen_api_secret": False,
                "pdfgen_workspace_identifier": False,
            }
        )
        sel = self.env["pdfgen.model.dataset"]._selection_default_template_id()
        self.assertEqual(sel, [])

    def test_selection_default_template_swallows_api_errors(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client = MagicMock()
        client.list_templates.side_effect = PdfGenApiError(503, "down")
        with patch(
            "odoo.addons.pdfgeneratorapi_connector.models.pdfgen_model_dataset.build_pdfgen_client",
            return_value=client,
        ):
            sel = self.env["pdfgen.model.dataset"]._selection_default_template_id()
        self.assertEqual(sel, [])

    def test_selection_default_template_handles_non_list_response(self):
        client = MagicMock()
        client.list_templates.return_value = {"response": "oops"}
        with patch(
            "odoo.addons.pdfgeneratorapi_connector.models.pdfgen_model_dataset.build_pdfgen_client",
            return_value=client,
        ):
            sel = self.env["pdfgen.model.dataset"]._selection_default_template_id()
        self.assertEqual(sel, [])

    def test_selection_default_template_skips_entries_without_id(self):
        client = MagicMock()
        client.list_templates.return_value = {
            "response": [{"name": "no-id"}, {"id": 7, "name": "Real"}]
        }
        with patch(
            "odoo.addons.pdfgeneratorapi_connector.models.pdfgen_model_dataset.build_pdfgen_client",
            return_value=client,
        ):
            sel = self.env["pdfgen.model.dataset"]._selection_default_template_id()
        self.assertEqual(sel, [("7", "Real")])

    # ------------------------------------------------------------------
    # Header-button launchers on the dataset form
    # ------------------------------------------------------------------

    def test_first_sample_record_returns_existing_partner_id(self):
        dataset = self._new_dataset()
        self.env["res.partner"].create({"name": "Acme for sample"})
        self.assertGreater(dataset._first_sample_record_id(), 0)

    def test_first_sample_record_returns_zero_when_no_records(self):
        """Edge case: dataset against a model whose table is empty."""
        empty_model = self.env.ref("base.model_res_users_log")
        dataset = self.env["pdfgen.model.dataset"].create(
            {"name": "Empty fixture", "model_id": empty_model.id}
        )
        # Wipe just in case anything's been logged in this transaction.
        self.env["res.users.log"].search([]).unlink()
        self.assertEqual(dataset._first_sample_record_id(), 0)

    def test_action_open_in_editor_returns_act_window_with_prefilled_context(self):
        dataset = self._new_dataset()
        # Ensure at least one partner exists so sample_record_id is prefilled too.
        self.env["res.partner"].create({"name": "Acme for editor"})
        action = dataset.action_open_in_editor()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "pdfgen.template.editor.wizard")
        self.assertEqual(action["context"]["default_dataset_id"], dataset.id)
        self.assertGreater(action["context"]["default_sample_record_id"], 0)
        # No default_template_id on this dataset → context omits it.
        self.assertNotIn("default_template_id", action["context"])

    def test_action_open_in_editor_forwards_default_template(self):
        dataset = self._new_dataset()
        with patch.object(
            type(dataset),
            "_selection_default_template_id",
            return_value=[("42", "Template 42")],
        ):
            dataset.default_template_id = "42"
        action = dataset.action_open_in_editor()
        self.assertEqual(action["context"]["default_template_id"], "42")

    def test_action_open_preview_without_template_opens_wizard_for_manual_pick(self):
        dataset = self._new_dataset()
        action = dataset.action_open_preview()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "pdfgen.coverage.wizard")
        self.assertEqual(action["target"], "new")
        self.assertEqual(action["context"]["default_dataset_id"], dataset.id)

    def test_action_open_preview_with_template_auto_renders(self):
        """When the dataset has a default_template_id, action_open_preview
        should create a coverage wizard and fire its `action_preview` so the
        user lands on a rendered preview instead of an empty form.
        """
        dataset = self._new_dataset()
        with patch.object(
            type(dataset),
            "_selection_default_template_id",
            return_value=[("42", "Template 42")],
        ):
            dataset.default_template_id = "42"
        with patch.object(
            self.env["pdfgen.coverage.wizard"].__class__,
            "action_preview",
            return_value={"type": "ir.actions.act_window", "tag": "fake-reopen"},
        ) as mock_preview:
            action = dataset.action_open_preview()
        mock_preview.assert_called_once()
        self.assertEqual(action["tag"], "fake-reopen")
