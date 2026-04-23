from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestCoverageWizard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("pdfgen.api_base_url", "https://us1.pdfgeneratorapi.com/api/v4")
        icp.set_param("pdfgen.api_key", "test-key")
        icp.set_param("pdfgen.api_secret", "test-secret")
        icp.set_param("pdfgen.workspace_identifier", "me@example.com")
        cls.partner_model = cls.env.ref("base.model_res_partner")
        cls.dataset = cls.env["pdfgen.model.dataset"].create(
            {
                "name": "Partner test dataset",
                "model_id": cls.partner_model.id,
            }
        )
        cls.env["pdfgen.model.dataset.line"].create(
            [
                {
                    "dataset_id": cls.dataset.id,
                    "placeholder_path": "name",
                    "odoo_field_path": "name",
                },
                {
                    "dataset_id": cls.dataset.id,
                    "placeholder_path": "vat",
                    "odoo_field_path": "vat",
                },
            ]
        )
        list_row = cls.env["pdfgen.model.dataset.line"].create(
            {
                "dataset_id": cls.dataset.id,
                "placeholder_path": "children",
                "is_list": True,
                "odoo_field_path": "child_ids",
            }
        )
        cls.env["pdfgen.model.dataset.line"].create(
            {
                "dataset_id": cls.dataset.id,
                "parent_id": list_row.id,
                "placeholder_path": "name",
                "odoo_field_path": "name",
            }
        )

    def _patch_client(self, client):
        return patch.object(
            self.env["pdfgen.coverage.wizard"].__class__,
            "_build_client",
            return_value=client,
        )

    def _new_wizard(self, template_id="42"):
        return self.env["pdfgen.coverage.wizard"].create(
            {"dataset_id": self.dataset.id, "template_id": template_id}
        )

    def test_all_covered_when_template_matches_dataset(self):
        wizard = self._new_wizard(template_id="100")
        client = MagicMock()
        client.get_template_data.return_value = {
            "response": {
                "name": "",
                "vat": "",
                "children": [{"name": ""}],
            }
        }
        client._request.return_value = {"response": {"name": "Nice template"}}
        with self._patch_client(client):
            wizard.action_check()
        self.assertTrue(wizard.checked)
        self.assertEqual(wizard.coverage_total, 3)
        self.assertEqual(wizard.coverage_matched, 3)
        self.assertFalse(wizard.missing_placeholders)
        self.assertFalse(wizard.extra_placeholders)
        self.assertEqual(wizard.template_name, "Nice template")

    def test_missing_placeholders_surface_when_template_needs_more(self):
        wizard = self._new_wizard(template_id="missing")
        client = MagicMock()
        client.get_template_data.return_value = {
            "response": {
                "name": "",
                "vat": "",
                "email": "",
                "phone": "",
                "children": [{"name": "", "age": ""}],
            }
        }
        client._request.side_effect = Exception("skip detail — not a PdfGenApiError")
        # Detail-fetch error is only caught for PdfGenApiError; make it a hit.
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client._request.side_effect = PdfGenApiError(500, "skip")
        with self._patch_client(client):
            wizard.action_check()
        missing = set(wizard.missing_placeholders.split("\n"))
        self.assertEqual(missing, {"email", "phone", "children[].age"})
        self.assertFalse(wizard.extra_placeholders)

    def test_extra_placeholders_surface_when_dataset_maps_unused(self):
        wizard = self._new_wizard(template_id="extras")
        client = MagicMock()
        client.get_template_data.return_value = {"response": {"name": ""}}
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client._request.side_effect = PdfGenApiError(500, "skip")
        with self._patch_client(client):
            wizard.action_check()
        extra = set(wizard.extra_placeholders.split("\n"))
        self.assertIn("vat", extra)
        self.assertIn("children[].name", extra)
        self.assertFalse(wizard.missing_placeholders)

    def test_api_error_wrapped_as_user_error(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        wizard = self._new_wizard(template_id="boom")
        client = MagicMock()
        client.get_template_data.side_effect = PdfGenApiError(404, "template not found")
        with self._patch_client(client), self.assertRaises(UserError) as ctx:
            wizard.action_check()
        self.assertIn("404", str(ctx.exception))

    def test_selection_template_id_live_list(self):
        client = MagicMock()
        client.list_templates.return_value = {
            "response": [
                {"id": 1, "name": "Invoice"},
                {"id": 2, "name": "Quote"},
            ],
        }
        with self._patch_client(client):
            selection = self.env["pdfgen.coverage.wizard"]._selection_template_id()
        self.assertEqual(selection, [("1", "Invoice"), ("2", "Quote")])

    def test_selection_template_id_returns_empty_when_unconfigured(self):
        icp = self.env["ir.config_parameter"].sudo()
        for key in ("pdfgen.api_key", "pdfgen.api_secret", "pdfgen.workspace_identifier"):
            icp.set_param(key, "")
        selection = self.env["pdfgen.coverage.wizard"]._selection_template_id()
        self.assertEqual(selection, [])

    def test_selection_template_id_skips_entries_without_id(self):
        client = MagicMock()
        client.list_templates.return_value = {
            "response": [
                {"id": 1, "name": "Valid"},
                {"name": "Missing id"},
                {"id": 2},
            ],
        }
        with self._patch_client(client):
            selection = self.env["pdfgen.coverage.wizard"]._selection_template_id()
        self.assertEqual(selection, [("1", "Valid"), ("2", "Template 2")])

    def test_selection_template_id_handles_non_list_response(self):
        client = MagicMock()
        client.list_templates.return_value = {"response": "oops"}
        with self._patch_client(client):
            selection = self.env["pdfgen.coverage.wizard"]._selection_template_id()
        self.assertEqual(selection, [])

    def test_selection_template_id_swallows_api_errors(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client = MagicMock()
        client.list_templates.side_effect = PdfGenApiError(503, "unavailable")
        with self._patch_client(client):
            selection = self.env["pdfgen.coverage.wizard"]._selection_template_id()
        self.assertEqual(selection, [])

    def test_action_check_errors_when_unconfigured(self):
        icp = self.env["ir.config_parameter"].sudo()
        for key in ("pdfgen.api_key", "pdfgen.api_secret", "pdfgen.workspace_identifier"):
            icp.set_param(key, "")
        wizard = self._new_wizard(template_id="99")
        with self.assertRaises(UserError) as ctx:
            wizard.action_check()
        self.assertIn("configured", str(ctx.exception).lower())

    def test_empty_list_response_is_treated_as_no_placeholders(self):
        """A blank template returns `{"response": []}`; should be coverage 0/0."""
        wizard = self._new_wizard(template_id="blank")
        client = MagicMock()
        client.get_template_data.return_value = {"response": []}
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client._request.side_effect = PdfGenApiError(500, "skip")
        with self._patch_client(client):
            wizard.action_check()
        self.assertEqual(wizard.coverage_total, 0)
        self.assertEqual(wizard.coverage_matched, 0)
        self.assertFalse(wizard.missing_placeholders)
        # Dataset lines all become "extras" — they're all unused by this empty template.
        self.assertTrue(wizard.extra_placeholders)
