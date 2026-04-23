import base64
from unittest.mock import MagicMock, patch

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests.common import tagged

PDF_B64 = base64.b64encode(b"%PDF-1.4 fake pdf bytes").decode()


@tagged("post_install", "-at_install")
class TestGeneratePdfWizard(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("pdfgen.api_base_url", "https://us1.pdfgeneratorapi.com/api/v4")
        icp.set_param("pdfgen.api_key", "test-key")
        icp.set_param("pdfgen.api_secret", "test-secret")
        icp.set_param("pdfgen.workspace_identifier", "me@example.com")
        cls.invoice = cls.init_invoice(
            "out_invoice",
            products=cls.product_a + cls.product_b,
            post=True,
        )
        cls.move_model = cls.env.ref("account.model_account_move")

    def _patch_client(self, client):
        return patch.object(
            self.env["pdfgen.generate.wizard"].__class__,
            "_build_client",
            return_value=client,
        )

    def test_selection_template_id_returns_tuples(self):
        client = MagicMock()
        client.list_templates.return_value = {
            "response": [
                {"id": 1, "name": "Invoice v1"},
                {"id": 2, "name": "Quote v1"},
            ],
        }
        with self._patch_client(client):
            selection = self.env["pdfgen.generate.wizard"]._selection_template_id()
        self.assertEqual(selection, [("1", "Invoice v1"), ("2", "Quote v1")])

    def test_selection_template_id_returns_empty_when_unconfigured(self):
        icp = self.env["ir.config_parameter"].sudo()
        for key in ("pdfgen.api_key", "pdfgen.api_secret", "pdfgen.workspace_identifier"):
            icp.set_param(key, "")
        selection = self.env["pdfgen.generate.wizard"]._selection_template_id()
        self.assertEqual(selection, [])

    def test_action_generate_uses_dataset_payload_and_closes(self):
        client = MagicMock()
        client.generate.return_value = {"response": PDF_B64}
        wizard = self.env["pdfgen.generate.wizard"].create(
            {"move_id": self.invoice.id, "template_id": "42"}
        )
        with self._patch_client(client):
            action = wizard.action_generate()
        client.generate.assert_called_once()
        data = client.generate.call_args.kwargs["data"]
        # Built from the seed dataset for account.move.
        self.assertEqual(data["invoice_number"], self.invoice.name)
        self.assertEqual(data["customer"]["name"], self.invoice.partner_id.name)
        self.assertEqual(action, {"type": "ir.actions.act_window_close"})
        attachment = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "account.move"),
                ("res_id", "=", self.invoice.id),
                ("mimetype", "=", "application/pdf"),
            ],
            limit=1,
        )
        self.assertTrue(attachment)
        self.assertEqual(base64.b64decode(attachment.datas), b"%PDF-1.4 fake pdf bytes")

    def test_action_generate_requires_dataset(self):
        # Archive the seed dataset to exercise the "no dataset" branch.
        dataset = self.env.ref("pdfgeneratorapi_connector.dataset_account_move")
        dataset.active = False
        wizard = self.env["pdfgen.generate.wizard"].create(
            {"move_id": self.invoice.id, "template_id": "999"}
        )
        with self.assertRaises(UserError) as ctx:
            wizard.action_generate()
        self.assertIn("dataset", str(ctx.exception).lower())
        dataset.active = True

    def test_action_generate_wraps_api_errors(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client = MagicMock()
        client.generate.side_effect = PdfGenApiError(500, "upstream boom")
        wizard = self.env["pdfgen.generate.wizard"].create(
            {"move_id": self.invoice.id, "template_id": "42"}
        )
        with self._patch_client(client), self.assertRaises(UserError) as ctx:
            wizard.action_generate()
        self.assertIn("500", str(ctx.exception))
        self.assertIn("upstream boom", str(ctx.exception))

    def test_action_generate_rejects_non_base64_payload(self):
        client = MagicMock()
        client.generate.return_value = {"response": "not valid base64 !!!"}
        wizard = self.env["pdfgen.generate.wizard"].create(
            {"move_id": self.invoice.id, "template_id": "42"}
        )
        with self._patch_client(client), self.assertRaises(UserError):
            wizard.action_generate()

    def test_action_generate_raises_on_unexpected_envelope(self):
        client = MagicMock()
        client.generate.return_value = {"unexpected_key": "abc"}
        wizard = self.env["pdfgen.generate.wizard"].create(
            {"move_id": self.invoice.id, "template_id": "42"}
        )
        with self._patch_client(client), self.assertRaises(UserError) as ctx:
            wizard.action_generate()
        self.assertIn("unexpected", str(ctx.exception).lower())

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
            selection = self.env["pdfgen.generate.wizard"]._selection_template_id()
        self.assertEqual(selection, [("1", "Valid"), ("2", "Template 2")])

    def test_selection_template_id_handles_non_list_response(self):
        client = MagicMock()
        client.list_templates.return_value = {"response": "oops"}
        with self._patch_client(client):
            selection = self.env["pdfgen.generate.wizard"]._selection_template_id()
        self.assertEqual(selection, [])

    def test_selection_template_id_swallows_api_errors(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client = MagicMock()
        client.list_templates.side_effect = PdfGenApiError(503, "unavailable")
        with self._patch_client(client):
            selection = self.env["pdfgen.generate.wizard"]._selection_template_id()
        self.assertEqual(selection, [])

    def test_extract_pdf_payload_handles_various_shapes(self):
        from odoo.addons.pdfgeneratorapi_connector.wizards.generate_pdf_wizard import (
            GeneratePdfWizard,
        )

        extract = GeneratePdfWizard._extract_pdf_payload
        self.assertEqual(extract("direct-string"), "direct-string")
        self.assertEqual(extract({"response": "val1"}), "val1")
        self.assertEqual(extract({"data": "val2"}), "val2")
        self.assertEqual(extract({"base64": "val3"}), "val3")
        self.assertEqual(extract({"response": {"base64": "nested"}}), "nested")
        self.assertEqual(extract({"data": {"content": "nested2"}}), "nested2")
        self.assertIsNone(extract(None))
        self.assertIsNone(extract(42))
        self.assertIsNone(extract({"nothing_useful": 1}))
