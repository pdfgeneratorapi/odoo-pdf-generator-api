from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.tests.common import tagged


@tagged("post_install", "-at_install")
class TestAccountMovePdfgen(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.invoice = cls.init_invoice("out_invoice", products=cls.product_a, post=True)

    def _set_credentials(self, *, configured):
        icp = self.env["ir.config_parameter"].sudo()
        icp.set_param("pdfgen.api_key", "k" if configured else "")
        icp.set_param("pdfgen.api_secret", "s" if configured else "")
        icp.set_param("pdfgen.workspace_identifier", "w" if configured else "")
        # Per-company creds override ICP (Phase F). Clear them in the
        # "unconfigured" case so the fallback sees genuinely-empty values.
        self.env.company.write(
            {
                "pdfgen_api_key": "k" if configured else False,
                "pdfgen_api_secret": "s" if configured else False,
                "pdfgen_workspace_identifier": "w" if configured else False,
            }
        )

    def test_pdfgen_configured_true_when_creds_present(self):
        self._set_credentials(configured=True)
        self.invoice.invalidate_recordset(["pdfgen_configured"])
        self.assertTrue(self.invoice.pdfgen_configured)

    def test_pdfgen_configured_false_when_creds_missing(self):
        self._set_credentials(configured=False)
        self.invoice.invalidate_recordset(["pdfgen_configured"])
        self.assertFalse(self.invoice.pdfgen_configured)

    def test_action_open_pdfgen_wizard_returns_form_action(self):
        action = self.invoice.action_open_pdfgen_wizard()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "pdfgen.generate.wizard")
        self.assertEqual(action["target"], "new")
        self.assertEqual(action["context"]["default_res_model"], "account.move")
        self.assertEqual(action["context"]["default_res_id"], self.invoice.id)

    def test_action_from_list_single_returns_action(self):
        action = self.invoice.action_open_pdfgen_wizard_from_list()
        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["res_model"], "pdfgen.generate.wizard")
        self.assertEqual(action["context"]["default_res_id"], self.invoice.id)

    def test_action_from_list_multi_opens_dispatch_wizard(self):
        another = self.init_invoice("out_invoice", products=self.product_a, post=True)
        recordset = self.invoice | another
        action = recordset.action_open_pdfgen_wizard_from_list()
        self.assertEqual(action["res_model"], "pdfgen.async.dispatch.wizard")
        self.assertEqual(set(action["context"]["active_ids"]), set(recordset.ids))

    def test_action_view_async_jobs_filters_by_selection(self):
        action = self.invoice.action_view_pdfgen_async_jobs_from_list()
        self.assertEqual(action["res_model"], "pdfgen.async.job")
        self.assertIn(("res_model", "=", "account.move"), action["domain"])
        self.assertIn(("res_id", "in", self.invoice.ids), action["domain"])

    def test_action_view_async_jobs_falls_back_to_model_when_empty(self):
        empty = self.env["account.move"]
        action = empty.action_view_pdfgen_async_jobs_from_list()
        self.assertEqual(action["res_model"], "pdfgen.async.job")
        self.assertEqual(action["domain"], [("res_model", "=", "account.move")])
