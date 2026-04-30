from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests.common import tagged


@tagged("post_install", "-at_install")
class TestModelDataset(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.move_model = cls.env.ref("account.model_account_move")
        cls.invoice = cls.init_invoice("out_invoice", products=cls.product_a, post=True)

    def _new_dataset(self, model_id=None):
        # The seed data already creates one for account.move, so for ad-hoc
        # cases we use res.partner to avoid the unique constraint.
        partner_model = self.env.ref("base.model_res_partner")
        return self.env["pdfgen.model.dataset"].create(
            {
                "name": "Partner dataset",
                "model_id": (model_id or partner_model.id),
            }
        )

    def test_seed_dataset_installed_for_account_move(self):
        dataset = self.env.ref("pdfgeneratorapi_connector.dataset_account_move")
        self.assertEqual(dataset.model, "account.move")
        # ~30 root + children lines — guard against accidental deletions.
        self.assertGreater(len(dataset.line_ids), 25)
        placeholders = dataset.line_ids.mapped("placeholder_path")
        self.assertIn("invoice_number", placeholders)
        self.assertIn("customer.name", placeholders)
        self.assertIn("lines", placeholders)
        # List section has children.
        lines_row = dataset.line_ids.filtered(lambda ln: ln.placeholder_path == "lines")
        self.assertTrue(lines_row.is_list)
        child_paths = lines_row.child_ids.mapped("placeholder_path")
        self.assertIn("description", child_paths)
        self.assertIn("quantity", child_paths)

    def test_resolve_payload_on_seeded_invoice_dataset(self):
        dataset = self.env.ref("pdfgeneratorapi_connector.dataset_account_move")
        payload = dataset.resolve_payload(self.invoice)
        self.assertEqual(payload["invoice_number"], self.invoice.name)
        self.assertEqual(payload["customer"]["name"], self.invoice.partner_id.name)
        self.assertEqual(payload["totals"]["total"], self.invoice.amount_total)
        # Expression row in seed: customer.full_address → "{street}, {city} {zip}".
        expected = (
            f"{self.invoice.partner_id.street or ''}, "
            f"{self.invoice.partner_id.city or ''} "
            f"{self.invoice.partner_id.zip or ''}"
        )
        self.assertEqual(payload["customer"]["full_address"], expected)
        # List section resolves with at least one invoice line.
        self.assertGreater(len(payload["lines"]), 0)
        # Each line item has the child placeholders defined in the seed.
        first_line = payload["lines"][0]
        self.assertIn("description", first_line)
        self.assertIn("quantity", first_line)

    def test_resolve_payload_rejects_wrong_model(self):
        dataset = self.env.ref("pdfgeneratorapi_connector.dataset_account_move")
        with self.assertRaises(UserError):
            dataset.resolve_payload(self.partner_a)

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
        partner_model = self.env.ref("base.model_res_partner")
        dataset = self.env["pdfgen.model.dataset"].create({"model_id": partner_model.id})
        self.assertTrue(dataset.name)

    def test_target_model_walks_parents_relation_path(self):
        """Children of a list row scope to the iterated record's model."""
        dataset = self.env.ref("pdfgeneratorapi_connector.dataset_account_move")
        # Seeded list header: placeholder_path='lines', odoo_field_path='invoice_line_ids'.
        lines_row = dataset.line_ids.filtered(lambda ln: ln.placeholder_path == "lines")
        self.assertTrue(lines_row)
        # Root row scopes to the dataset's model.
        self.assertEqual(lines_row.target_model, "account.move")
        # Any child of that row scopes to account.move.line.
        child = lines_row.child_ids[0]
        self.assertEqual(child.target_model, "account.move.line")

    def test_target_model_falls_back_when_parent_path_invalid(self):
        partner_model = self.env.ref("base.model_res_partner")
        dataset = self.env["pdfgen.model.dataset"].create(
            {"name": "X", "model_id": partner_model.id}
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
        from unittest.mock import MagicMock, patch

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
        from unittest.mock import MagicMock, patch

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
        from unittest.mock import MagicMock, patch

        client = MagicMock()
        client.list_templates.return_value = {"response": "oops"}
        with patch(
            "odoo.addons.pdfgeneratorapi_connector.models.pdfgen_model_dataset.build_pdfgen_client",
            return_value=client,
        ):
            sel = self.env["pdfgen.model.dataset"]._selection_default_template_id()
        self.assertEqual(sel, [])

    def test_selection_default_template_skips_entries_without_id(self):
        from unittest.mock import MagicMock, patch

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
