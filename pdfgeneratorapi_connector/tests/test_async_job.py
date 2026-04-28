import hashlib
import hmac

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestPdfgenAsyncJob(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("pdfgen.webhook_secret", "shhh")
        icp.set_param("pdfgen.webhook_base_url", "https://odoo.example.com")

    def _new_job(self, **overrides):
        partner = self.env["res.partner"].create({"name": "Acme"})
        vals = {
            "name": "Invoice — Acme",
            "template_id": "42",
            "template_name": "Invoice",
            "res_model": "res.partner",
            "res_id": partner.id,
        }
        vals.update(overrides)
        return self.env["pdfgen.async.job"].create(vals)

    def test_callback_url_embeds_job_id_and_token(self):
        job = self._new_job()
        url = job.callback_url()
        self.assertIn(f"j={job.id}", url)
        expected = hmac.new(b"shhh", str(job.id).encode(), hashlib.sha256).hexdigest()
        self.assertIn(f"t={expected}", url)
        self.assertTrue(url.startswith("https://odoo.example.com/pdfgen/webhook/deliver"))

    def test_verify_token_accepts_match(self):
        job = self._new_job()
        good = hmac.new(b"shhh", str(job.id).encode(), hashlib.sha256).hexdigest()
        self.assertTrue(job.verify_token(good))

    def test_verify_token_rejects_tamper(self):
        job = self._new_job()
        self.assertFalse(job.verify_token("nope"))
        self.assertFalse(job.verify_token(""))
        self.assertFalse(job.verify_token(None))

    def test_callback_url_falls_back_to_web_base_url(self):
        self.env["ir.config_parameter"].sudo().set_param("pdfgen.webhook_base_url", "")
        self.env["ir.config_parameter"].sudo().set_param(
            "web.base.url", "https://fallback.example.com"
        )
        job = self._new_job()
        self.assertTrue(job.callback_url().startswith("https://fallback.example.com/"))

    def test_res_display_resolves_record(self):
        job = self._new_job()
        partner = self.env[job.res_model].browse(job.res_id)
        self.assertEqual(job.res_display, partner.display_name)

    def test_res_display_blank_when_record_gone(self):
        job = self._new_job()
        self.env[job.res_model].browse(job.res_id).unlink()
        job.invalidate_recordset()
        self.assertEqual(job.res_display, "")

    def test_callback_url_raises_when_secret_missing(self):
        from odoo.exceptions import UserError

        self.env["ir.config_parameter"].sudo().set_param("pdfgen.webhook_secret", "")
        self.env.company.write({"pdfgen_webhook_secret": False})
        job = self._new_job()
        with self.assertRaises(UserError):
            job.callback_url()
