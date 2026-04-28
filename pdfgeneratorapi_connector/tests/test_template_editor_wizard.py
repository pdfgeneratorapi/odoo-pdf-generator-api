from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestTemplateEditorWizard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("pdfgen.api_base_url", "https://us1.pdfgeneratorapi.com/api/v4")
        icp.set_param("pdfgen.api_key", "test-key")
        icp.set_param("pdfgen.api_secret", "test-secret")
        icp.set_param("pdfgen.workspace_identifier", "me@example.com")

    def _patch_client(self, client):
        return patch.object(
            self.env["pdfgen.template.editor.wizard"].__class__,
            "_build_client",
            return_value=client,
        )

    def _new_wizard(self, **vals):
        return self.env["pdfgen.template.editor.wizard"].create(vals)

    def test_open_editor_stores_url_on_wizard(self):
        client = MagicMock()
        client.open_editor.return_value = "https://us1.pdfgeneratorapi.com/editor/42?token=abc"
        wizard = self._new_wizard(template_id="42")
        with self._patch_client(client):
            wizard.action_open_editor()
        client.open_editor.assert_called_once_with("42", data=None)
        self.assertEqual(wizard.editor_url, "https://us1.pdfgeneratorapi.com/editor/42?token=abc")

    def test_open_editor_without_template_raises(self):
        wizard = self._new_wizard()
        with self.assertRaises(UserError) as ctx:
            wizard.action_open_editor()
        self.assertIn("template", str(ctx.exception).lower())

    def test_open_editor_api_error_wrapped(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client = MagicMock()
        client.open_editor.side_effect = PdfGenApiError(403, "forbidden")
        wizard = self._new_wizard(template_id="42")
        with self._patch_client(client), self.assertRaises(UserError) as ctx:
            wizard.action_open_editor()
        self.assertIn("403", str(ctx.exception))

    def test_open_editor_empty_url_raises(self):
        client = MagicMock()
        client.open_editor.return_value = None
        wizard = self._new_wizard(template_id="42")
        with self._patch_client(client), self.assertRaises(UserError) as ctx:
            wizard.action_open_editor()
        self.assertIn("no url", str(ctx.exception).lower())

    def test_create_template_creates_and_opens_editor(self):
        client = MagicMock()
        client.create_template.return_value = {"response": {"id": 99, "name": "Brand new"}}
        client.open_editor.return_value = "https://us1.pdfgeneratorapi.com/editor/99?token=xyz"
        wizard = self._new_wizard(new_template_name="Brand new")
        with self._patch_client(client):
            wizard.action_create_template()
        client.create_template.assert_called_once_with("Brand new")
        client.open_editor.assert_called_once_with("99", data=None)
        self.assertEqual(wizard.template_id, "99")
        self.assertEqual(wizard.editor_url, "https://us1.pdfgeneratorapi.com/editor/99?token=xyz")

    def test_create_template_defaults_name_when_blank(self):
        client = MagicMock()
        client.create_template.return_value = {"response": {"id": 1, "name": "New template"}}
        client.open_editor.return_value = "https://us1.pdfgeneratorapi.com/editor/1?token=xyz"
        wizard = self._new_wizard(new_template_name="")
        with self._patch_client(client):
            wizard.action_create_template()
        called_name = client.create_template.call_args.args[0]
        self.assertTrue(called_name)
        self.assertIsInstance(called_name, str)

    def test_create_template_api_error_wrapped(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client = MagicMock()
        client.create_template.side_effect = PdfGenApiError(500, "boom")
        wizard = self._new_wizard(new_template_name="X")
        with self._patch_client(client), self.assertRaises(UserError) as ctx:
            wizard.action_create_template()
        self.assertIn("500", str(ctx.exception))

    def test_create_template_missing_id_raises(self):
        client = MagicMock()
        client.create_template.return_value = {"response": {"name": "No id here"}}
        wizard = self._new_wizard(new_template_name="X")
        with self._patch_client(client), self.assertRaises(UserError) as ctx:
            wizard.action_create_template()
        self.assertIn("no id", str(ctx.exception).lower())

    def test_selection_live_from_api(self):
        client = MagicMock()
        client.list_templates.return_value = {
            "response": [{"id": 1, "name": "Invoice"}, {"id": 2, "name": "Quote"}],
        }
        with self._patch_client(client):
            sel = self.env["pdfgen.template.editor.wizard"]._selection_template_id()
        self.assertEqual(sel, [("1", "Invoice"), ("2", "Quote")])

    def test_selection_empty_when_unconfigured(self):
        icp = self.env["ir.config_parameter"].sudo()
        for key in ("pdfgen.api_key", "pdfgen.api_secret", "pdfgen.workspace_identifier"):
            icp.set_param(key, "")
        # Phase F: company-level creds win — wipe those too.
        self.env.company.write(
            {
                "pdfgen_api_key": False,
                "pdfgen_api_secret": False,
                "pdfgen_workspace_identifier": False,
            }
        )
        sel = self.env["pdfgen.template.editor.wizard"]._selection_template_id()
        self.assertEqual(sel, [])

    def test_action_open_editor_when_unconfigured(self):
        icp = self.env["ir.config_parameter"].sudo()
        for key in ("pdfgen.api_key", "pdfgen.api_secret", "pdfgen.workspace_identifier"):
            icp.set_param(key, "")
        # Company-level creds override ICP. Wipe those too so the client
        # build actually sees an unconfigured environment.
        self.env.company.write(
            {
                "pdfgen_api_key": False,
                "pdfgen_api_secret": False,
                "pdfgen_workspace_identifier": False,
            }
        )
        wizard = self._new_wizard(template_id="1")
        with self.assertRaises(UserError) as ctx:
            wizard.action_open_editor()
        self.assertIn("configured", str(ctx.exception).lower())

    def test_selection_swallows_api_errors(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client = MagicMock()
        client.list_templates.side_effect = PdfGenApiError(503, "down")
        with self._patch_client(client):
            sel = self.env["pdfgen.template.editor.wizard"]._selection_template_id()
        self.assertEqual(sel, [])

    def test_selection_non_list_response(self):
        client = MagicMock()
        client.list_templates.return_value = {"response": "oops"}
        with self._patch_client(client):
            sel = self.env["pdfgen.template.editor.wizard"]._selection_template_id()
        self.assertEqual(sel, [])

    def _new_partner_dataset(self):
        partner_model = self.env.ref("base.model_res_partner")
        dataset = self.env["pdfgen.model.dataset"].create(
            {"name": "Partner dataset", "model_id": partner_model.id}
        )
        self.env["pdfgen.model.dataset.line"].create(
            {
                "dataset_id": dataset.id,
                "placeholder_path": "name",
                "odoo_field_path": "name",
            }
        )
        return dataset

    def test_open_editor_passes_resolved_data(self):
        dataset = self._new_partner_dataset()
        partner = self.env["res.partner"].create({"name": "Acme"})
        client = MagicMock()
        client.open_editor.return_value = "https://us1.pdfgeneratorapi.com/editor/42?token=abc"
        wizard = self._new_wizard(
            template_id="42",
            dataset_id=dataset.id,
            sample_record_id=partner.id,
        )
        with self._patch_client(client):
            wizard.action_open_editor()
        client.open_editor.assert_called_once_with("42", data={"name": "Acme"})

    def test_open_editor_no_data_without_record(self):
        dataset = self._new_partner_dataset()
        client = MagicMock()
        client.open_editor.return_value = "https://us1.pdfgeneratorapi.com/editor/42?token=abc"
        wizard = self._new_wizard(template_id="42", dataset_id=dataset.id)
        with self._patch_client(client):
            wizard.action_open_editor()
        client.open_editor.assert_called_once_with("42", data=None)

    def test_open_editor_no_data_when_record_gone(self):
        dataset = self._new_partner_dataset()
        partner = self.env["res.partner"].create({"name": "Tmp"})
        partner_id = partner.id
        partner.unlink()
        client = MagicMock()
        client.open_editor.return_value = "https://us1.pdfgeneratorapi.com/editor/42?token=abc"
        wizard = self._new_wizard(
            template_id="42",
            dataset_id=dataset.id,
            sample_record_id=partner_id,
        )
        with self._patch_client(client):
            wizard.action_open_editor()
        client.open_editor.assert_called_once_with("42", data=None)

    def test_action_open_sample_record_returns_form_action(self):
        dataset = self._new_partner_dataset()
        partner = self.env["res.partner"].create({"name": "Acme"})
        wizard = self._new_wizard(dataset_id=dataset.id, sample_record_id=partner.id)
        action = wizard.action_open_sample_record()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "res.partner")
        self.assertEqual(action["res_id"], partner.id)
        self.assertEqual(action["target"], "new")

    def test_action_open_sample_record_without_record_raises(self):
        dataset = self._new_partner_dataset()
        wizard = self._new_wizard(dataset_id=dataset.id)
        with self.assertRaises(UserError):
            wizard.action_open_sample_record()

    def test_onchange_dataset_clears_sample(self):
        dataset_a = self._new_partner_dataset()
        users_model = self.env.ref("base.model_res_users")
        dataset_b = self.env["pdfgen.model.dataset"].create(
            {"name": "Users dataset", "model_id": users_model.id}
        )
        partner = self.env["res.partner"].create({"name": "Acme"})
        wizard = self.env["pdfgen.template.editor.wizard"].new(
            {"dataset_id": dataset_a.id, "sample_record_id": partner.id}
        )
        wizard.dataset_id = dataset_b
        wizard._onchange_dataset_id()
        self.assertFalse(wizard.sample_record_id)
