from unittest.mock import MagicMock, patch

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests.common import tagged


@tagged("post_install", "-at_install")
class TestTemplateMapping(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("pdfgen.api_base_url", "https://us1.pdfgeneratorapi.com/api/v4")
        icp.set_param("pdfgen.api_key", "k")
        icp.set_param("pdfgen.api_secret", "s")
        icp.set_param("pdfgen.workspace_identifier", "w@example.com")
        cls.move_model = cls.env.ref("account.model_account_move")
        cls.invoice = cls.init_invoice("out_invoice", products=cls.product_a, post=True)

    def _new_mapping(self, template_id="100"):
        return self.env["pdfgen.template.mapping"].create(
            {
                "name": f"Mapping {template_id}",
                "template_id": template_id,
                "model_id": self.move_model.id,
            }
        )

    def test_load_placeholders_creates_lines_and_children(self):
        mapping = self._new_mapping(template_id="200")
        client = MagicMock()
        client.get_template_data.return_value = {
            "response": {
                "invoice_number": "",
                "customer": {"name": "", "vat": ""},
                "lines": [{"desc": "", "qty": ""}],
            }
        }
        client._request.return_value = {"response": {"name": "Remote Template"}}
        with patch.object(type(mapping), "_build_client", return_value=client):
            mapping.action_load_placeholders()
        paths = mapping.line_ids.filtered(lambda ln: not ln.parent_id).mapped("placeholder_path")
        self.assertIn("invoice_number", paths)
        self.assertIn("customer.name", paths)
        self.assertIn("customer.vat", paths)
        self.assertIn("lines", paths)
        lines_header = mapping.line_ids.filtered(lambda ln: ln.placeholder_path == "lines")
        self.assertTrue(lines_header.is_list)
        child_paths = lines_header.child_ids.mapped("placeholder_path")
        self.assertIn("desc", child_paths)
        self.assertIn("qty", child_paths)
        self.assertEqual(mapping.template_name, "Remote Template")

    def test_load_placeholders_replaces_existing_lines(self):
        mapping = self._new_mapping(template_id="201")
        self.env["pdfgen.template.mapping.line"].create(
            {"mapping_id": mapping.id, "placeholder_path": "old"}
        )
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client = MagicMock()
        client.get_template_data.return_value = {"response": {"new_field": ""}}
        client._request.side_effect = PdfGenApiError(500, "skip detail fetch")
        with patch.object(type(mapping), "_build_client", return_value=client):
            mapping.action_load_placeholders()
        paths = mapping.line_ids.mapped("placeholder_path")
        self.assertIn("new_field", paths)
        self.assertNotIn("old", paths)

    def test_load_placeholders_errors_when_unconfigured(self):
        icp = self.env["ir.config_parameter"].sudo()
        for key in ("pdfgen.api_key", "pdfgen.api_secret", "pdfgen.workspace_identifier"):
            icp.set_param(key, "")
        mapping = self._new_mapping(template_id="202")
        with self.assertRaises(UserError):
            mapping.action_load_placeholders()

    def test_load_placeholders_wraps_api_errors(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        mapping = self._new_mapping(template_id="203")
        client = MagicMock()
        client.get_template_data.side_effect = PdfGenApiError(404, "not found")
        with (
            patch.object(type(mapping), "_build_client", return_value=client),
            self.assertRaises(UserError) as ctx,
        ):
            mapping.action_load_placeholders()
        self.assertIn("404", str(ctx.exception))

    def test_load_placeholders_rejects_non_dict_shape(self):
        mapping = self._new_mapping(template_id="204")
        client = MagicMock()
        client.get_template_data.return_value = {"response": "oops not a dict"}
        with (
            patch.object(type(mapping), "_build_client", return_value=client),
            self.assertRaises(UserError),
        ):
            mapping.action_load_placeholders()

    def test_resolve_payload_rejects_wrong_model(self):
        mapping = self._new_mapping(template_id="205")
        with self.assertRaises(UserError):
            mapping.resolve_payload(self.partner_a)

    def test_resolve_payload_builds_expected_dict(self):
        mapping = self._new_mapping(template_id="206")
        self.env["pdfgen.template.mapping.line"].create(
            [
                {
                    "mapping_id": mapping.id,
                    "placeholder_path": "num",
                    "odoo_field_path": "name",
                },
                {
                    "mapping_id": mapping.id,
                    "placeholder_path": "customer.display",
                    "odoo_field_path": "partner_id.display_name",
                },
            ]
        )
        payload = mapping.resolve_payload(self.invoice)
        self.assertEqual(payload["num"], self.invoice.name)
        self.assertEqual(payload["customer"]["display"], self.invoice.partner_id.display_name)
