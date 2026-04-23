import base64
from unittest.mock import MagicMock, patch

from odoo.addons.sale.tests.common import SaleCommon
from odoo.tests.common import tagged

PDF_B64 = base64.b64encode(b"%PDF-1.4 fake quotation pdf").decode()


@tagged("post_install", "-at_install")
class TestSaleOrderPdfgen(SaleCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("pdfgen.api_base_url", "https://us1.pdfgeneratorapi.com/api/v4")
        icp.set_param("pdfgen.api_key", "test-key")
        icp.set_param("pdfgen.api_secret", "test-secret")
        icp.set_param("pdfgen.workspace_identifier", "me@example.com")

    def test_pdfgen_configured_exposed_on_sale_order(self):
        self.assertIn(
            "pdfgen_configured",
            self.env["sale.order"]._fields,
            "sale.order must inherit pdfgen.document.mixin",
        )
        self.assertTrue(self.sale_order.pdfgen_configured)

    def test_action_open_pdfgen_wizard_targets_sale_order(self):
        action = self.sale_order.action_open_pdfgen_wizard()
        self.assertEqual(action["res_model"], "pdfgen.generate.wizard")
        self.assertEqual(action["context"]["default_res_model"], "sale.order")
        self.assertEqual(action["context"]["default_res_id"], self.sale_order.id)

    def test_seed_dataset_installed(self):
        dataset = self.env.ref("pdfgeneratorapi_connector_sale.dataset_sale_order")
        self.assertEqual(dataset.model, "sale.order")
        placeholders = dataset.line_ids.mapped("placeholder_path")
        self.assertIn("order_number", placeholders)
        self.assertIn("customer.name", placeholders)
        self.assertIn("lines", placeholders)
        lines_row = dataset.line_ids.filtered(lambda ln: ln.placeholder_path == "lines")
        self.assertTrue(lines_row.is_list)
        self.assertIn("quantity", lines_row.child_ids.mapped("placeholder_path"))

    def test_resolve_payload_builds_quotation_json(self):
        dataset = self.env.ref("pdfgeneratorapi_connector_sale.dataset_sale_order")
        payload = dataset.resolve_payload(self.sale_order)
        self.assertEqual(payload["order_number"], self.sale_order.name)
        self.assertEqual(payload["customer"]["name"], self.sale_order.partner_id.name)
        self.assertEqual(payload["totals"]["total"], self.sale_order.amount_total)
        self.assertGreater(len(payload["lines"]), 0)
        self.assertIn("product", payload["lines"][0])
        self.assertIn("quantity", payload["lines"][0])

    def test_generate_wizard_end_to_end_on_sale_order(self):
        client = MagicMock()
        client.generate.return_value = {"response": PDF_B64}
        wizard = self.env["pdfgen.generate.wizard"].create(
            {
                "res_model": "sale.order",
                "res_id": self.sale_order.id,
                "template_id": "42",
            }
        )
        with patch.object(
            self.env["pdfgen.generate.wizard"].__class__,
            "_build_client",
            return_value=client,
        ):
            wizard.action_generate()
        data = client.generate.call_args.kwargs["data"]
        self.assertEqual(data["order_number"], self.sale_order.name)
        attachment = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "sale.order"),
                ("res_id", "=", self.sale_order.id),
                ("mimetype", "=", "application/pdf"),
            ],
            limit=1,
        )
        self.assertTrue(attachment)
