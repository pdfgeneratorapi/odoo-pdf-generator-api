import base64
from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged

PDF_B64 = base64.b64encode(b"%PDF-1.4 fake delivery slip").decode()


@tagged("post_install", "-at_install")
class TestStockPickingPdfgen(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("pdfgen.api_base_url", "https://us1.pdfgeneratorapi.com/api/v4")
        icp.set_param("pdfgen.api_key", "test-key")
        icp.set_param("pdfgen.api_secret", "test-secret")
        icp.set_param("pdfgen.workspace_identifier", "me@example.com")
        cls.partner = cls.env["res.partner"].create({"name": "Stock Target"})
        cls.product = cls.env["product.product"].create({"name": "Widget", "type": "consu"})
        cls.picking_type = cls.env.ref("stock.picking_type_out")
        cls.picking = cls.env["stock.picking"].create(
            {
                "partner_id": cls.partner.id,
                "picking_type_id": cls.picking_type.id,
                "location_id": cls.env.ref("stock.stock_location_stock").id,
                "location_dest_id": cls.env.ref("stock.stock_location_customers").id,
                "move_ids": [
                    (
                        0,
                        0,
                        {
                            # Odoo 18 requires stock.move.name (Description);
                            # 19 defaults it, so the move can omit it there.
                            "name": cls.product.name,
                            "product_id": cls.product.id,
                            "product_uom_qty": 4.0,
                            "product_uom": cls.product.uom_id.id,
                            "location_id": cls.env.ref("stock.stock_location_stock").id,
                            "location_dest_id": cls.env.ref("stock.stock_location_customers").id,
                        },
                    ),
                ],
            }
        )

    def test_pdfgen_configured_exposed_on_stock_picking(self):
        self.assertIn(
            "pdfgen_configured",
            self.env["stock.picking"]._fields,
            "stock.picking must inherit pdfgen.document.mixin",
        )
        self.assertTrue(self.picking.pdfgen_configured)

    def test_action_open_pdfgen_wizard_targets_stock_picking(self):
        action = self.picking.action_open_pdfgen_wizard()
        self.assertEqual(action["res_model"], "pdfgen.generate.wizard")
        self.assertEqual(action["context"]["default_res_model"], "stock.picking")
        self.assertEqual(action["context"]["default_res_id"], self.picking.id)

    def test_seed_dataset_installed(self):
        dataset = self.env.ref("pdfgeneratorapi_connector_stock.dataset_stock_picking")
        self.assertEqual(dataset.model, "stock.picking")
        placeholders = dataset.line_ids.mapped("placeholder_path")
        self.assertIn("reference", placeholders)
        self.assertIn("partner.name", placeholders)
        self.assertIn("lines", placeholders)
        lines_row = dataset.line_ids.filtered(lambda ln: ln.placeholder_path == "lines")
        self.assertTrue(lines_row.is_list)
        child_paths = lines_row.child_ids.mapped("placeholder_path")
        self.assertIn("product", child_paths)
        self.assertIn("demand", child_paths)

    def test_resolve_payload_builds_picking_json(self):
        dataset = self.env.ref("pdfgeneratorapi_connector_stock.dataset_stock_picking")
        payload = dataset.resolve_payload(self.picking)
        self.assertEqual(payload["reference"], self.picking.name)
        self.assertEqual(payload["partner"]["name"], "Stock Target")
        self.assertEqual(payload["operation_type"], self.picking_type.name)
        self.assertGreater(len(payload["lines"]), 0)
        first = payload["lines"][0]
        self.assertIn("product", first)
        self.assertIn("demand", first)
        self.assertEqual(first["demand"], 4.0)

    def test_generate_wizard_end_to_end_on_stock_picking(self):
        client = MagicMock()
        client.generate.return_value = {"response": PDF_B64}
        wizard = self.env["pdfgen.generate.wizard"].create(
            {
                "res_model": "stock.picking",
                "res_id": self.picking.id,
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
        self.assertEqual(data["reference"], self.picking.name)
        attachment = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "stock.picking"),
                ("res_id", "=", self.picking.id),
                ("mimetype", "=", "application/pdf"),
            ],
            limit=1,
        )
        self.assertTrue(attachment)
