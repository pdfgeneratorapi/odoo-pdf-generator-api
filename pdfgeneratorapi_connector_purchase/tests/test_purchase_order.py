import base64
from unittest.mock import MagicMock, patch

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests.common import TransactionCase, tagged

PDF_B64 = base64.b64encode(b"%PDF-1.4 fake po pdf").decode()


@tagged("post_install", "-at_install")
class TestPurchaseOrderPdfgen(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("pdfgen.api_base_url", "https://us1.pdfgeneratorapi.com/api/v4")
        icp.set_param("pdfgen.api_key", "test-key")
        icp.set_param("pdfgen.api_secret", "test-secret")
        icp.set_param("pdfgen.workspace_identifier", "me@example.com")
        cls.purchase_order = cls.env["purchase.order"].create(
            {
                "partner_id": cls.partner_a.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": cls.product_a.id,
                            "name": cls.product_a.name,
                            "product_qty": 3.0,
                            "product_uom_id": cls.product_a.uom_id.id,
                            "price_unit": 75.0,
                        },
                    ),
                ],
            }
        )

    def test_pdfgen_configured_exposed_on_purchase_order(self):
        self.assertIn(
            "pdfgen_configured",
            self.env["purchase.order"]._fields,
            "purchase.order must inherit pdfgen.document.mixin",
        )
        self.assertTrue(self.purchase_order.pdfgen_configured)

    def test_action_open_pdfgen_wizard_targets_purchase_order(self):
        action = self.purchase_order.action_open_pdfgen_wizard()
        self.assertEqual(action["res_model"], "pdfgen.generate.wizard")
        self.assertEqual(action["context"]["default_res_model"], "purchase.order")
        self.assertEqual(action["context"]["default_res_id"], self.purchase_order.id)

    def test_seed_dataset_installed(self):
        dataset = self.env.ref("pdfgeneratorapi_connector_purchase.dataset_purchase_order")
        self.assertEqual(dataset.model, "purchase.order")
        placeholders = dataset.line_ids.mapped("placeholder_path")
        self.assertIn("order_number", placeholders)
        self.assertIn("vendor.name", placeholders)
        self.assertIn("lines", placeholders)
        lines_row = dataset.line_ids.filtered(lambda ln: ln.placeholder_path == "lines")
        self.assertTrue(lines_row.is_list)
        child_paths = lines_row.child_ids.mapped("placeholder_path")
        self.assertIn("quantity", child_paths)
        self.assertIn("product", child_paths)

    def test_resolve_payload_builds_purchase_json(self):
        dataset = self.env.ref("pdfgeneratorapi_connector_purchase.dataset_purchase_order")
        payload = dataset.resolve_payload(self.purchase_order)
        self.assertEqual(payload["order_number"], self.purchase_order.name)
        self.assertEqual(payload["vendor"]["name"], self.purchase_order.partner_id.name)
        self.assertEqual(payload["totals"]["total"], self.purchase_order.amount_total)
        self.assertGreater(len(payload["lines"]), 0)
        first = payload["lines"][0]
        self.assertIn("product", first)
        self.assertIn("quantity", first)
        self.assertEqual(first["quantity"], 3.0)

    def test_generate_wizard_end_to_end_on_purchase_order(self):
        client = MagicMock()
        client.generate.return_value = {"response": PDF_B64}
        wizard = self.env["pdfgen.generate.wizard"].create(
            {
                "res_model": "purchase.order",
                "res_id": self.purchase_order.id,
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
        self.assertEqual(data["order_number"], self.purchase_order.name)
        attachment = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "purchase.order"),
                ("res_id", "=", self.purchase_order.id),
                ("mimetype", "=", "application/pdf"),
            ],
            limit=1,
        )
        self.assertTrue(attachment)


@tagged("post_install", "-at_install")
class TestPurchaseListButtonReach(TransactionCase):
    """Purchase has three independent primary root list views with no common
    ancestor, so each needs its own extension. Inheriting only
    `purchase_order_tree` put the button on RFQs and left Purchase Orders
    (`purchase_order_view_tree`) without it.
    """

    def _has_button(self, xmlid):
        view = self.env.ref(xmlid)
        arch = self.env["purchase.order"].get_view(view.id, "list")["arch"]
        return "action_open_pdfgen_wizard_from_list" in arch

    def test_button_reaches_every_purchase_order_list(self):
        for xmlid in (
            "purchase.purchase_order_view_tree",  # Purchase > Purchase Orders
            "purchase.purchase_order_tree",  # RFQs (default view)
            "purchase.purchase_order_kpis_tree",
        ):
            self.assertTrue(self._has_button(xmlid), f"Generate button missing from {xmlid}")
