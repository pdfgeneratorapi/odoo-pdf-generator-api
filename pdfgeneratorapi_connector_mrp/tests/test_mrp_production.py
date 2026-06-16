import base64
from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged

PDF_B64 = base64.b64encode(b"%PDF-1.4 fake mrp pdf").decode()


@tagged("post_install", "-at_install")
class TestMrpProductionPdfgen(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("pdfgen.api_base_url", "https://us1.pdfgeneratorapi.com/api/v4")
        icp.set_param("pdfgen.api_key", "test-key")
        icp.set_param("pdfgen.api_secret", "test-secret")
        icp.set_param("pdfgen.workspace_identifier", "me@example.com")
        cls.product = cls.env["product.product"].create(
            {"name": "Finished Gadget", "type": "product"}
        )
        cls.mo = cls.env["mrp.production"].create(
            {
                "product_id": cls.product.id,
                "product_qty": 5.0,
                "product_uom_id": cls.product.uom_id.id,
            }
        )

    def test_pdfgen_configured_exposed_on_mrp_production(self):
        self.assertIn(
            "pdfgen_configured",
            self.env["mrp.production"]._fields,
            "mrp.production must inherit pdfgen.document.mixin",
        )
        self.assertTrue(self.mo.pdfgen_configured)

    def test_action_open_pdfgen_wizard_targets_mrp_production(self):
        action = self.mo.action_open_pdfgen_wizard()
        self.assertEqual(action["res_model"], "pdfgen.generate.wizard")
        self.assertEqual(action["context"]["default_res_model"], "mrp.production")
        self.assertEqual(action["context"]["default_res_id"], self.mo.id)

    def test_seed_dataset_installed(self):
        dataset = self.env.ref("pdfgeneratorapi_connector_mrp.dataset_mrp_production")
        self.assertEqual(dataset.model, "mrp.production")
        placeholders = dataset.line_ids.mapped("placeholder_path")
        self.assertIn("reference", placeholders)
        self.assertIn("product.name", placeholders)
        self.assertIn("components", placeholders)
        components_row = dataset.line_ids.filtered(lambda ln: ln.placeholder_path == "components")
        self.assertTrue(components_row.is_list)
        child_paths = components_row.child_ids.mapped("placeholder_path")
        self.assertIn("product", child_paths)
        self.assertIn("demand", child_paths)

    def test_resolve_payload_builds_mrp_json(self):
        dataset = self.env.ref("pdfgeneratorapi_connector_mrp.dataset_mrp_production")
        payload = dataset.resolve_payload(self.mo)
        self.assertEqual(payload["reference"], self.mo.name)
        self.assertEqual(payload["product"]["name"], self.product.display_name)
        self.assertEqual(payload["product"]["quantity"], 5.0)
        # No components on a freshly created MO with no BOM — list should be empty.
        self.assertEqual(payload["components"], [])

    def test_generate_wizard_end_to_end_on_mrp_production(self):
        client = MagicMock()
        client.generate.return_value = {"response": PDF_B64}
        wizard = self.env["pdfgen.generate.wizard"].create(
            {
                "res_model": "mrp.production",
                "res_id": self.mo.id,
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
        self.assertEqual(data["reference"], self.mo.name)
        attachment = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "mrp.production"),
                ("res_id", "=", self.mo.id),
                ("mimetype", "=", "application/pdf"),
            ],
            limit=1,
        )
        self.assertTrue(attachment)
