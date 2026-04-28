import base64
import hashlib
import hmac
import json

from odoo.tests.common import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestWebhookController(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("pdfgen.webhook_secret", "shhh")
        base_url = cls.env["ir.config_parameter"].sudo().get_param("web.base.url")
        icp.set_param("pdfgen.webhook_base_url", base_url)
        cls.partner = cls.env["res.partner"].create({"name": "Acme webhook"})
        cls.job = cls.env["pdfgen.async.job"].create(
            {
                "name": "Invoice — Acme",
                "template_id": "42",
                "template_name": "Invoice",
                "res_model": "res.partner",
                "res_id": cls.partner.id,
                "state": "dispatched",
                "pdfgen_job_id": "remote-1",
            }
        )

    def _token(self, job_id):
        return hmac.new(b"shhh", str(job_id).encode(), hashlib.sha256).hexdigest()

    def _post(self, body, *, job_id=None, token=None):
        job_id = job_id if job_id is not None else self.job.id
        token = token if token is not None else self._token(self.job.id)
        return self.url_open(
            f"/pdfgen/webhook/deliver?j={job_id}&t={token}",
            data=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )

    def test_valid_token_attaches_pdf_and_completes(self):
        pdf_b64 = base64.b64encode(b"%PDF-1.4 fake").decode()
        response = self._post({"id": "remote-1", "response": pdf_b64})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.job.invalidate_recordset()
        self.assertEqual(self.job.state, "completed")
        self.assertTrue(self.job.attachment_id)
        self.assertEqual(self.job.attachment_id.res_model, "res.partner")
        self.assertEqual(self.job.attachment_id.res_id, self.partner.id)

    def test_bad_token_rejected(self):
        pdf_b64 = base64.b64encode(b"%PDF").decode()
        response = self._post({"response": pdf_b64}, token="wrong")
        self.assertEqual(response.status_code, 403)
        self.job.invalidate_recordset()
        self.assertEqual(self.job.state, "dispatched")

    def test_unknown_job_rejected(self):
        response = self._post({}, job_id=999999)
        self.assertEqual(response.status_code, 404)

    def test_pdfgen_id_mismatch_rejected(self):
        pdf_b64 = base64.b64encode(b"%PDF").decode()
        response = self._post({"id": "different", "response": pdf_b64})
        self.assertEqual(response.status_code, 403)
        self.job.invalidate_recordset()
        self.assertEqual(self.job.state, "dispatched")

    def test_completed_job_is_idempotent(self):
        self.job.write({"state": "completed"})
        response = self._post({"response": base64.b64encode(b"%PDF").decode()})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body.get("note"), "already-completed")

    def test_error_payload_marks_failed(self):
        response = self._post({"id": "remote-1", "status": "failed", "error": "render exploded"})
        self.assertEqual(response.status_code, 200)
        self.job.invalidate_recordset()
        self.assertEqual(self.job.state, "failed")
        self.assertIn("render exploded", self.job.error or "")

    def test_missing_payload_marks_failed(self):
        response = self._post({"id": "remote-1"})
        self.assertEqual(response.status_code, 400)
        self.job.invalidate_recordset()
        self.assertEqual(self.job.state, "failed")

    def test_missing_token_returns_400(self):
        response = self.url_open(
            f"/pdfgen/webhook/deliver?j={self.job.id}",
            data="{}",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)

    def test_non_integer_job_id_returns_400(self):
        response = self.url_open(
            "/pdfgen/webhook/deliver?j=abc&t=anything",
            data="{}",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_base64_marks_failed(self):
        response = self._post({"id": "remote-1", "response": "@@@not-base64@@@"})
        self.assertEqual(response.status_code, 400)
        self.job.invalidate_recordset()
        self.assertEqual(self.job.state, "failed")
        self.assertIn("base64", (self.job.error or "").lower())

    def test_invalid_json_body_marks_failed(self):
        # Valid token, malformed JSON body — controller treats payload as {} so
        # it falls into the no-payload path.
        response = self.url_open(
            f"/pdfgen/webhook/deliver?j={self.job.id}&t={self._token(self.job.id)}",
            data="not-json",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)

    def test_missing_source_record_marks_failed(self):
        # Job whose res_id was deleted between dispatch and callback.
        partner = self.env["res.partner"].create({"name": "tmp"})
        partner_id = partner.id
        partner.unlink()
        gone_job = self.env["pdfgen.async.job"].create(
            {
                "name": "Invoice — tmp",
                "template_id": "1",
                "template_name": "Invoice",
                "res_model": "res.partner",
                "res_id": partner_id,
                "state": "dispatched",
                "pdfgen_job_id": "remote-2",
            }
        )
        pdf_b64 = base64.b64encode(b"%PDF").decode()
        response = self.url_open(
            f"/pdfgen/webhook/deliver?j={gone_job.id}&t={self._token(gone_job.id)}",
            data=json.dumps({"id": "remote-2", "response": pdf_b64}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 500)
        gone_job.invalidate_recordset()
        self.assertEqual(gone_job.state, "failed")
