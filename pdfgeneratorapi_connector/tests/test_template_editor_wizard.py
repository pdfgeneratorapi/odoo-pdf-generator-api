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

    def test_open_editor_routes_magic_value_to_create_path(self):
        """Picking the '+ Create new template' dropdown entry and clicking
        Open should mint the template (via the create API) and then open
        the editor — single button, no separate Create step."""
        client = MagicMock()
        client.create_template.return_value = {"response": {"id": 77, "name": "Magic"}}
        client.open_editor.return_value = "https://us1.pdfgeneratorapi.com/editor/77?token=ok"
        wizard = self._new_wizard(template_id="__new__", new_template_name="Magic")
        with self._patch_client(client):
            wizard.action_open_editor()
        client.create_template.assert_called_once_with("Magic")
        self.assertEqual(wizard.template_id, "77")
        self.assertEqual(wizard.editor_url, "https://us1.pdfgeneratorapi.com/editor/77?token=ok")

    def test_open_editor_magic_value_without_name_raises(self):
        """Magic dropdown entry needs an accompanying name — otherwise the
        Open button should fail loudly instead of trying to create an
        anonymous template."""
        wizard = self._new_wizard(template_id="__new__", new_template_name="")
        with self.assertRaises(UserError) as ctx:
            wizard.action_open_editor()
        self.assertIn("name", str(ctx.exception).lower())

    def test_onchange_template_id_clears_stale_name(self):
        """Switching from '+ Create new template' to a real template should
        wipe any half-typed new name, so it doesn't lurk in the form and
        re-trigger creation on the next Open click."""
        wizard = self._new_wizard(template_id="__new__", new_template_name="left over")
        wizard.template_id = "1"
        wizard._onchange_template_id()
        self.assertFalse(wizard.new_template_name)

    def test_selection_live_from_api(self):
        """API list call succeeds → magic '+ Create new template' entry comes
        first, followed by every live template in order."""
        client = MagicMock()
        client.list_templates.return_value = {
            "response": [{"id": 1, "name": "Invoice"}, {"id": 2, "name": "Quote"}],
        }
        with self._patch_client(client):
            sel = self.env["pdfgen.template.editor.wizard"]._selection_template_id()
        self.assertEqual(sel[0][0], "__new__")
        self.assertIn("Create new template", sel[0][1])
        self.assertEqual(sel[1:], [("1", "Invoice"), ("2", "Quote")])

    def test_selection_shows_create_affordance_on_empty_workspace(self):
        """Even when the workspace has zero templates the dropdown surfaces
        the '+ Create new template' entry so a fresh setup can mint its
        first template without leaving the editor."""
        client = MagicMock()
        client.list_templates.return_value = {"response": []}
        with self._patch_client(client):
            sel = self.env["pdfgen.template.editor.wizard"]._selection_template_id()
        self.assertEqual(sel, [("__new__", "+ Create new template…")])

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

    def test_selection_includes_library_section_between_create_and_own(self):
        """Library ("Default") templates slot between the magic create entry
        and the account's own templates, with `lib:`-prefixed values the
        grouped dropdown widget keys off."""
        client = MagicMock()
        client.list_templates.return_value = {"response": [{"id": 1, "name": "Mine"}]}
        client.list_library_templates.return_value = {
            "response": [
                {"id": "pub-a", "name": "Library invoice"},
                {"id": "pub-b", "name": "Library quote"},
            ],
        }
        with self._patch_client(client):
            sel = self.env["pdfgen.template.editor.wizard"]._selection_template_id()
        self.assertEqual(sel[0][0], "__new__")
        self.assertEqual(
            sel[1:],
            [
                ("lib:pub-a", "Library invoice"),
                ("lib:pub-b", "Library quote"),
                ("1", "Mine"),
            ],
        )

    def test_selection_filters_library_to_the_odoo_tag(self):
        """The public library serves every integration's templates; only the
        `odoo`-tagged ones target the datasets this connector ships, so the
        dropdown must ask the API to filter rather than listing all of them."""
        client = MagicMock()
        client.list_templates.return_value = {"response": []}
        client.list_library_templates.return_value = {"response": []}
        with self._patch_client(client):
            self.env["pdfgen.template.editor.wizard"]._selection_template_id()
        client.list_library_templates.assert_called_once_with(tags="odoo")

    def test_selection_library_failure_keeps_own_templates(self):
        """The library section is additive — a dead library endpoint must
        not take down the rest of the dropdown."""
        client = MagicMock()
        client.list_templates.return_value = {"response": [{"id": 1, "name": "Mine"}]}
        client.list_library_templates.side_effect = Exception("library down")
        with self._patch_client(client):
            sel = self.env["pdfgen.template.editor.wizard"]._selection_template_id()
        self.assertEqual(sel[1:], [("1", "Mine")])

    def test_open_editor_copies_library_template_then_opens(self):
        """Opening a Default Template copies its definition into the account
        (library templates are read-only upstream) and opens the editor on
        the fresh copy."""
        definition = {
            "id": "pub-a",
            "name": "Library invoice",
            "layout": {"format": "A4"},
            "pages": [],
        }
        client = MagicMock()
        client.get_library_template.return_value = {"response": definition}
        client.create_template.return_value = {"response": {"id": 55, "name": "Library invoice"}}
        client.open_editor.return_value = "https://us1.pdfgeneratorapi.com/editor/55?token=cp"
        wizard = self._new_wizard(template_id="lib:pub-a")
        with self._patch_client(client):
            wizard.action_open_editor()
        client.get_library_template.assert_called_once_with("pub-a")
        client.create_template.assert_called_once_with(definition=definition)
        client.open_editor.assert_called_once_with("55", data=None)
        self.assertEqual(wizard.template_id, "55")
        self.assertEqual(wizard.editor_url, "https://us1.pdfgeneratorapi.com/editor/55?token=cp")

    def test_copy_library_template_fetch_error_wrapped(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client = MagicMock()
        client.get_library_template.side_effect = PdfGenApiError(404, "gone")
        wizard = self._new_wizard(template_id="lib:pub-a")
        with self._patch_client(client), self.assertRaises(UserError) as ctx:
            wizard.action_open_editor()
        self.assertIn("404", str(ctx.exception))

    def test_copy_library_template_create_error_wrapped(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client = MagicMock()
        client.get_library_template.return_value = {"response": {"name": "X"}}
        client.create_template.side_effect = PdfGenApiError(422, "invalid definition")
        wizard = self._new_wizard(template_id="lib:pub-a")
        with self._patch_client(client), self.assertRaises(UserError) as ctx:
            wizard.action_open_editor()
        self.assertIn("422", str(ctx.exception))

    def test_copy_library_template_missing_id_raises(self):
        client = MagicMock()
        client.get_library_template.return_value = {"response": {"name": "X"}}
        client.create_template.return_value = {"response": {"name": "no id"}}
        wizard = self._new_wizard(template_id="lib:pub-a")
        with self._patch_client(client), self.assertRaises(UserError) as ctx:
            wizard.action_open_editor()
        self.assertIn("no id", str(ctx.exception).lower())

    def test_is_library_template_computed(self):
        wizard = self._new_wizard(template_id="lib:pub-a")
        self.assertTrue(wizard.is_library_template)
        wizard = self._new_wizard(template_id="42")
        self.assertFalse(wizard.is_library_template)

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

    def test_onchange_dataset_autopicks_first_record_of_new_model(self):
        """Switching dataset auto-fills `sample_record_id` with the first
        record of the new dataset's model so the editor renders against
        real data without the user picking a record manually."""
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
        # Test DB always has at least the admin user, so this is non-zero.
        self.assertTrue(wizard.sample_record_id)
        self.assertEqual(
            self.env["res.users"].browse(wizard.sample_record_id).exists(),
            self.env["res.users"].browse(wizard.sample_record_id),
        )

    def test_onchange_dataset_clears_sample_when_dataset_unset(self):
        """Clearing the dataset wipes the sample record too — without a model
        to scope against, a leftover record id would be meaningless."""
        dataset = self._new_partner_dataset()
        partner = self.env["res.partner"].create({"name": "Acme"})
        wizard = self.env["pdfgen.template.editor.wizard"].new(
            {"dataset_id": dataset.id, "sample_record_id": partner.id}
        )
        wizard.dataset_id = False
        wizard._onchange_dataset_id()
        self.assertFalse(wizard.sample_record_id)
