"""Tests that lock in the seeded `account.move` dataset.

These tests live in the invoicing bridge because the seed XML and the
sample data they rely on are owned by this addon. The generic dataset
model tests (resolver fall-back, expression-beats-path, etc.) stay in
the base addon's test suite and use `res.partner` as a fixture.
"""

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests.common import tagged


@tagged("post_install", "-at_install")
class TestAccountDataset(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.invoice = cls.init_invoice("out_invoice", products=cls.product_a, post=True)

    def test_seed_dataset_installed_for_account_move(self):
        dataset = self.env.ref("pdfgeneratorapi_connector_account.dataset_account_move")
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
        dataset = self.env.ref("pdfgeneratorapi_connector_account.dataset_account_move")
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
        dataset = self.env.ref("pdfgeneratorapi_connector_account.dataset_account_move")
        with self.assertRaises(UserError):
            dataset.resolve_payload(self.partner_a)

    def test_target_model_walks_parents_relation_path(self):
        """Children of a list row scope to the iterated record's model."""
        dataset = self.env.ref("pdfgeneratorapi_connector_account.dataset_account_move")
        # Seeded list header: placeholder_path='lines', odoo_field_path='invoice_line_ids'.
        lines_row = dataset.line_ids.filtered(lambda ln: ln.placeholder_path == "lines")
        self.assertTrue(lines_row)
        # Root row scopes to the dataset's model.
        self.assertEqual(lines_row.target_model, "account.move")
        # Any child of that row scopes to account.move.line.
        child = lines_row.child_ids[0]
        self.assertEqual(child.target_model, "account.move.line")
