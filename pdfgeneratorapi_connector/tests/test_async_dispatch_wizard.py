from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPdfgenAsyncDispatchWizard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("pdfgen.api_base_url", "https://us1.pdfgeneratorapi.com/api/v4")
        icp.set_param("pdfgen.api_key", "k")
        icp.set_param("pdfgen.api_secret", "s")
        icp.set_param("pdfgen.workspace_identifier", "w")
        icp.set_param("pdfgen.webhook_secret", "shhh")
        icp.set_param("pdfgen.webhook_base_url", "https://odoo.example.com")

    def _patch_client(self, client):
        return patch.object(
            self.env["pdfgen.async.dispatch.wizard"].__class__,
            "_build_client",
            return_value=client,
        )

    def _new_dataset(self):
        partner_model = self.env.ref("base.model_res_partner")
        dataset = self.env["pdfgen.model.dataset"].create(
            {"name": "Partner dataset", "model_id": partner_model.id}
        )
        self.env["pdfgen.model.dataset.line"].create(
            {
                "dataset_id": dataset.id,
                "placeholder_path": "name",
                "odoo_field_path": "name",
            }
        )
        return dataset

    def test_dispatch_creates_one_job_per_record(self):
        self._new_dataset()
        partners = self.env["res.partner"].create([{"name": "A"}, {"name": "B"}, {"name": "C"}])
        client = MagicMock()
        client.generate_async.side_effect = ["job-a", "job-b", "job-c"]
        wiz = self.env["pdfgen.async.dispatch.wizard"].create(
            {
                "res_model": "res.partner",
                "res_ids": ",".join(str(p.id) for p in partners),
                "template_id": "42",
            }
        )
        with self._patch_client(client):
            action = wiz.action_dispatch()
        self.assertEqual(client.generate_async.call_count, 3)
        # Each call gets a distinct callback URL with the job id baked in.
        callbacks = [c.kwargs["callback_url"] for c in client.generate_async.call_args_list]
        self.assertEqual(len(callbacks), len(set(callbacks)))
        for url in callbacks:
            self.assertIn("/pdfgen/webhook/deliver?", url)
        # Action redirects to the just-created jobs.
        self.assertEqual(action["res_model"], "pdfgen.async.job")
        job_ids = list(action["domain"][0][2])
        jobs = self.env["pdfgen.async.job"].browse(job_ids)
        self.assertEqual(len(jobs), 3)
        for job in jobs:
            self.assertEqual(job.state, "dispatched")
            self.assertTrue(job.pdfgen_job_id)
            self.assertTrue(job.dispatched_at)

    def test_dispatch_marks_failed_when_api_raises(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        self._new_dataset()
        partner = self.env["res.partner"].create({"name": "X"})
        client = MagicMock()
        client.generate_async.side_effect = PdfGenApiError(500, "boom")
        wiz = self.env["pdfgen.async.dispatch.wizard"].create(
            {
                "res_model": "res.partner",
                "res_ids": str(partner.id),
                "template_id": "42",
            }
        )
        with self._patch_client(client):
            wiz.action_dispatch()
        job = self.env["pdfgen.async.job"].search(
            [("res_model", "=", "res.partner"), ("res_id", "=", partner.id)],
            limit=1,
        )
        self.assertEqual(job.state, "failed")
        self.assertIn("500", job.error or "")

    def test_dispatch_requires_dataset(self):
        # No dataset for res.country → friendly UserError.
        country = self.env.ref("base.us")
        wiz = self.env["pdfgen.async.dispatch.wizard"].create(
            {
                "res_model": "res.country",
                "res_ids": str(country.id),
                "template_id": "42",
            }
        )
        with self._patch_client(MagicMock()), self.assertRaises(UserError) as ctx:
            wiz.action_dispatch()
        self.assertIn("dataset", str(ctx.exception).lower())

    def test_record_count_computes_from_res_ids(self):
        # `.new()` skips the NOT NULL constraint on `template_id` — we only
        # care about the compute, not persistence.
        wiz = self.env["pdfgen.async.dispatch.wizard"].new(
            {"res_model": "res.partner", "res_ids": "1,2,3,4"}
        )
        self.assertEqual(wiz.record_count, 4)

    def test_dispatch_unknown_model_raises(self):
        wiz = self.env["pdfgen.async.dispatch.wizard"].new(
            {"res_model": "nope.no.such", "res_ids": "1", "template_id": "42"}
        )
        with self.assertRaises(UserError) as ctx:
            wiz.action_dispatch()
        self.assertIn("unknown", str(ctx.exception).lower())

    def test_dispatch_empty_records_raises(self):
        self._new_dataset()
        wiz = self.env["pdfgen.async.dispatch.wizard"].create(
            {"res_model": "res.partner", "res_ids": "", "template_id": "42"}
        )
        with self._patch_client(MagicMock()), self.assertRaises(UserError) as ctx:
            wiz.action_dispatch()
        self.assertIn("no records", str(ctx.exception).lower())

    def test_dispatch_marks_failed_on_unexpected_exception(self):
        self._new_dataset()
        partner = self.env["res.partner"].create({"name": "Y"})
        client = MagicMock()
        client.generate_async.side_effect = RuntimeError("kaboom")
        wiz = self.env["pdfgen.async.dispatch.wizard"].create(
            {"res_model": "res.partner", "res_ids": str(partner.id), "template_id": "42"}
        )
        with self._patch_client(client):
            wiz.action_dispatch()
        job = self.env["pdfgen.async.job"].search(
            [("res_model", "=", "res.partner"), ("res_id", "=", partner.id)], limit=1
        )
        self.assertEqual(job.state, "failed")
        self.assertIn("kaboom", job.error or "")

    def test_selection_returns_live_template_list(self):
        client = MagicMock()
        client.list_templates.return_value = {
            "response": [{"id": 1, "name": "Invoice"}, {"id": 2, "name": "Quote"}],
        }
        with self._patch_client(client):
            sel = self.env["pdfgen.async.dispatch.wizard"]._selection_template_id()
        self.assertEqual(sel, [("1", "Invoice"), ("2", "Quote")])

    def test_selection_empty_when_unconfigured(self):
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
        sel = self.env["pdfgen.async.dispatch.wizard"]._selection_template_id()
        self.assertEqual(sel, [])

    def test_selection_swallows_api_errors(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client = MagicMock()
        client.list_templates.side_effect = PdfGenApiError(503, "down")
        with self._patch_client(client):
            sel = self.env["pdfgen.async.dispatch.wizard"]._selection_template_id()
        self.assertEqual(sel, [])

    def test_selection_non_list_response(self):
        client = MagicMock()
        client.list_templates.return_value = {"response": "oops"}
        with self._patch_client(client):
            sel = self.env["pdfgen.async.dispatch.wizard"]._selection_template_id()
        self.assertEqual(sel, [])

    def test_default_get_pulls_template_from_dataset_default(self):
        dataset = self._new_dataset()
        self.env.cr.execute(
            "UPDATE pdfgen_model_dataset SET default_template_id=%s WHERE id=%s",
            ("88", dataset.id),
        )
        dataset.invalidate_recordset()
        defaults = (
            self.env["pdfgen.async.dispatch.wizard"]
            .with_context(active_model="res.partner", active_ids=[1])
            .default_get(["res_model", "res_ids", "template_id"])
        )
        self.assertEqual(defaults["template_id"], "88")

    def test_default_get_pulls_active_ids_from_context(self):
        wiz = (
            self.env["pdfgen.async.dispatch.wizard"]
            .with_context(
                active_model="res.partner",
                active_ids=[7, 8],
            )
            .default_get(["res_model", "res_ids"])
        )
        self.assertEqual(wiz["res_model"], "res.partner")
        self.assertEqual(wiz["res_ids"], "7,8")
