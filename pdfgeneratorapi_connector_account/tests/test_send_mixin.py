"""Tests for `pdfgen.send.mixin` via the concrete `account.move.send.wizard`
inheritance.
"""

import base64
from unittest.mock import MagicMock, patch

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import UserError
from odoo.tests.common import tagged

PDF_B64 = base64.b64encode(b"%PDF-1.4 fake").decode()
HTML_B64 = base64.b64encode(b"<html><body>Preview</body></html>").decode()

# Patch target for the mixin's client builder. Hoisted to a module constant
# because the dotted path is too long to fit on one line under our 100-char
# budget and ruff format keeps re-joining string-concat splits.
_BUILD_CLIENT = (
    "odoo.addons.pdfgeneratorapi_connector.models." + "pdfgen_send_mixin.build_pdfgen_client"
)


@tagged("post_install", "-at_install")
class TestPdfgenSendMixin(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("pdfgen.api_base_url", "https://us1.pdfgeneratorapi.com/api/v4")
        icp.set_param("pdfgen.api_key", "k")
        icp.set_param("pdfgen.api_secret", "s")
        icp.set_param("pdfgen.workspace_identifier", "w")
        cls.invoice = cls.init_invoice("out_invoice", products=cls.product_a, post=True)
        cls.dataset = cls.env.ref("pdfgeneratorapi_connector_account.dataset_account_move")

    def setUp(self):
        super().setUp()
        # init_invoice(post=True) may have left a pdfgen-marked attachment on
        # the move from an earlier in-class test (savepoint rollback doesn't
        # undo class-level setup). Clear them and the dataset default so each
        # test starts from a known baseline.
        self.env["ir.attachment"].search(
            [
                ("res_model", "=", self.invoice._name),
                ("res_id", "=", self.invoice.id),
                ("description", "=like", "pdfgen:%"),
            ]
        ).unlink()
        self.env.cr.execute(
            "UPDATE pdfgen_model_dataset SET default_template_id=NULL WHERE id=%s",
            (self.dataset.id,),
        )
        self.dataset.invalidate_recordset()

    def _wizard(self):
        return self.env["account.move.send.wizard"].new({"move_id": self.invoice.id})

    def _set_dataset_default_template(self, tid):
        # Selection has a dynamic getter; bypass validation by writing via SQL
        # so the test doesn't need a live API to populate the choices list.
        self.env.cr.execute(
            "UPDATE pdfgen_model_dataset SET default_template_id=%s WHERE id=%s",
            (tid, self.dataset.id),
        )
        self.dataset.invalidate_recordset()

    def _attach(self, *, description, days_ago=0):
        from datetime import timedelta

        from odoo import fields

        att = self.env["ir.attachment"].create(
            {
                "name": f"att-{description or 'standard'}.pdf",
                "type": "binary",
                "datas": PDF_B64,
                "res_model": self.invoice._name,
                "res_id": self.invoice.id,
                "mimetype": "application/pdf",
                "description": description,
            }
        )
        if days_ago:
            self.env.cr.execute(
                "UPDATE ir_attachment SET create_date=%s WHERE id=%s",
                (fields.Datetime.now() - timedelta(days=days_ago), att.id),
            )
            att.invalidate_recordset()
        return att

    # ------------------------------------------------------- look-up helpers

    def test_latest_pdfgen_attachment_returns_newest_marker(self):
        wiz = self._wizard()
        old = self._attach(description="pdfgen:template:old", days_ago=2)
        new = self._attach(description="pdfgen:template:new", days_ago=0)
        latest = wiz._pdfgen_latest_pdfgen_attachment(self.invoice)
        self.assertEqual(latest, new)
        self.assertNotEqual(latest, old)

    def test_latest_standard_attachment_ignores_pdfgen_marker(self):
        wiz = self._wizard()
        self._attach(description="pdfgen:template:1")
        std = self._attach(description=False)
        latest = wiz._pdfgen_latest_standard_attachment(self.invoice)
        self.assertEqual(latest, std)

    # ---------------------------------------------------- template chain

    def test_pick_template_id_from_existing_attachment(self):
        wiz = self._wizard()
        self._attach(description="pdfgen:template:42")
        self.assertEqual(wiz._pdfgen_pick_template_id(self.invoice), "42")

    def test_pick_template_id_falls_back_to_dataset_default(self):
        wiz = self._wizard()
        self._set_dataset_default_template("99")
        self.assertEqual(wiz._pdfgen_pick_template_id(self.invoice), "99")

    def test_pick_template_id_returns_false_when_nothing(self):
        wiz = self._wizard()
        self.assertFalse(wiz._pdfgen_pick_template_id(self.invoice))

    # ---------------------------------------------------- toggle default

    def test_should_default_on_when_pdfgen_newer(self):
        wiz = self._wizard()
        self._attach(description=False, days_ago=2)  # standard older
        self._attach(description="pdfgen:template:1", days_ago=0)
        self.assertTrue(wiz._pdfgen_should_default_on(self.invoice))

    def test_should_default_off_when_standard_newer(self):
        wiz = self._wizard()
        self._attach(description="pdfgen:template:1", days_ago=2)
        self._attach(description=False, days_ago=0)
        self.assertFalse(wiz._pdfgen_should_default_on(self.invoice))

    def test_should_default_on_with_dataset_default_only(self):
        wiz = self._wizard()
        self._set_dataset_default_template("99")
        self.assertTrue(wiz._pdfgen_should_default_on(self.invoice))

    def test_should_default_off_with_no_pdfgen_and_no_default(self):
        wiz = self._wizard()
        self._attach(description=False)  # only standard report
        self.assertFalse(wiz._pdfgen_should_default_on(self.invoice))

    # ---------------------------------------------------- preview render

    def test_preview_embeds_existing_pdfgen_attachment(self):
        wiz = self._wizard()
        att = self._attach(description="pdfgen:template:42")
        # No client mock needed — preview should reuse the attachment.
        with patch(_BUILD_CLIENT) as build:
            html = wiz._pdfgen_render_preview_html("42", self.invoice)
        self.assertIn(f"/web/content/{att.id}", html)
        self.assertIn("<iframe", html)
        build.assert_not_called()

    def test_preview_falls_back_to_api_when_template_changed(self):
        # Existing attachment was for template "old"; user picks "new" → no
        # iframe shortcut, falls through to the HTML API render path.
        wiz = self._wizard()
        self._attach(description="pdfgen:template:old")
        client = MagicMock()
        client.generate.return_value = {"response": HTML_B64}
        with patch(_BUILD_CLIENT, return_value=client):
            html = wiz._pdfgen_render_preview_html("new", self.invoice)
        self.assertIn("Preview", html)
        client.generate.assert_called_once()

    def test_preview_html_decodes_response(self):
        wiz = self._wizard()
        client = MagicMock()
        client.generate.return_value = {"response": HTML_B64}
        with patch(
            _BUILD_CLIENT,
            return_value=client,
        ):
            html = wiz._pdfgen_render_preview_html("42", self.invoice)
        self.assertIn("Preview", html)
        client.generate.assert_called_once()
        self.assertEqual(client.generate.call_args.kwargs["fmt"], "html")

    def test_preview_returns_empty_on_api_error(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        wiz = self._wizard()
        client = MagicMock()
        client.generate.side_effect = PdfGenApiError(500, "boom")
        with patch(
            _BUILD_CLIENT,
            return_value=client,
        ):
            html = wiz._pdfgen_render_preview_html("42", self.invoice)
        self.assertEqual(html, "")

    # ---------------------------------------------------- generation

    def test_generate_attachment_creates_pdfgen_marker(self):
        wiz = self._wizard()
        client = MagicMock()
        client.generate.return_value = {"response": PDF_B64}
        with patch(
            _BUILD_CLIENT,
            return_value=client,
        ):
            att = wiz._pdfgen_generate_attachment("42", self.invoice)
        self.assertEqual(att.res_model, "account.move")
        self.assertEqual(att.res_id, self.invoice.id)
        self.assertTrue(att.description.startswith("pdfgen:template:42"))
        self.assertEqual(att.mimetype, "application/pdf")

    def test_generate_attachment_wraps_api_error_as_user_error(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        wiz = self._wizard()
        client = MagicMock()
        client.generate.side_effect = PdfGenApiError(500, "boom")
        with patch(_BUILD_CLIENT, return_value=client), self.assertRaises(UserError):
            wiz._pdfgen_generate_attachment("42", self.invoice)

    # ---------------------------------------------------- edge paths

    def test_dataset_lookup_empty_for_no_record(self):
        wiz = self._wizard()
        self.assertFalse(wiz._pdfgen_dataset(self.env["account.move"]))

    def test_latest_attachment_helpers_empty_for_no_record(self):
        wiz = self._wizard()
        self.assertFalse(wiz._pdfgen_latest_pdfgen_attachment(self.env["account.move"]))
        self.assertFalse(wiz._pdfgen_latest_standard_attachment(self.env["account.move"]))

    def test_should_default_off_for_no_record(self):
        wiz = self._wizard()
        self.assertFalse(wiz._pdfgen_should_default_on(self.env["account.move"]))

    def test_pick_template_skips_malformed_description(self):
        wiz = self._wizard()
        # Marker without :template:<id> shouldn't yield a tid; falls through
        # to the dataset default (None here).
        self._attach(description="pdfgen:other:weird")
        self.assertFalse(wiz._pdfgen_pick_template_id(self.invoice))

    def test_preview_returns_empty_when_no_inputs(self):
        wiz = self._wizard()
        self.assertEqual(wiz._pdfgen_render_preview_html(False, self.invoice), "")
        self.assertEqual(wiz._pdfgen_render_preview_html("42", self.env["account.move"]), "")

    def test_preview_returns_empty_when_no_dataset(self):
        wiz = self._wizard()
        # Switch the seeded dataset off — the lookup falls back to empty.
        self.dataset.active = False
        try:
            self.assertEqual(wiz._pdfgen_render_preview_html("42", self.invoice), "")
        finally:
            self.dataset.active = True

    def test_preview_returns_empty_when_payload_missing(self):
        wiz = self._wizard()
        client = MagicMock()
        client.generate.return_value = {"unexpected": "shape"}
        with patch(
            _BUILD_CLIENT,
            return_value=client,
        ):
            html = wiz._pdfgen_render_preview_html("42", self.invoice)
        self.assertEqual(html, "")

    def test_generate_attachment_requires_template(self):
        wiz = self._wizard()
        with self.assertRaises(UserError):
            wiz._pdfgen_generate_attachment(False, self.invoice)

    def test_generate_attachment_requires_dataset(self):
        wiz = self._wizard()
        self.dataset.active = False
        try:
            with self.assertRaises(UserError):
                wiz._pdfgen_generate_attachment("42", self.invoice)
        finally:
            self.dataset.active = True

    def test_generate_attachment_rejects_unrecognised_response(self):
        wiz = self._wizard()
        client = MagicMock()
        client.generate.return_value = {"unexpected": "shape"}
        with patch(_BUILD_CLIENT, return_value=client), self.assertRaises(UserError):
            wiz._pdfgen_generate_attachment("42", self.invoice)

    def test_generate_attachment_rejects_invalid_base64(self):
        wiz = self._wizard()
        client = MagicMock()
        client.generate.return_value = {"response": "###not-base64###"}
        with patch(_BUILD_CLIENT, return_value=client), self.assertRaises(UserError):
            wiz._pdfgen_generate_attachment("42", self.invoice)

    def test_generate_attachment_replace_policy_clears_old(self):
        wiz = self._wizard()
        old = self._attach(description="pdfgen:template:old")
        self.env["ir.config_parameter"].sudo().set_param("pdfgen.attachment_cleanup", "replace")
        client = MagicMock()
        client.generate.return_value = {"response": PDF_B64}
        with patch(
            _BUILD_CLIENT,
            return_value=client,
        ):
            wiz._pdfgen_generate_attachment("42", self.invoice)
        # Old attachment is gone, fresh one exists.
        self.assertFalse(old.exists())

    def test_extract_payload_envelope_variants(self):
        extract = self.env["pdfgen.send.mixin"]._pdfgen_extract_payload
        self.assertEqual(extract("ABC"), "ABC")
        self.assertEqual(extract({"response": "ABC"}), "ABC")
        self.assertEqual(extract({"response": {"base64": "ABC"}}), "ABC")
        self.assertEqual(extract({"response": {"content": "ABC"}}), "ABC")
        self.assertEqual(extract({"response": {"data": "ABC"}}), "ABC")
        self.assertEqual(extract({"data": "ABC"}), "ABC")
        self.assertEqual(extract({"base64": "ABC"}), "ABC")
        self.assertIsNone(extract({"response": {"foo": "bar"}}))
        self.assertIsNone(extract(123))
        self.assertIsNone(extract({}))

    # ---------------------------------------------------- wizard composition

    def test_wizard_pdfgen_configured_true_when_dataset_present(self):
        wiz = self._wizard()
        self.assertTrue(wiz.pdfgen_configured)

    def test_wizard_pdfgen_use_custom_default_off_with_no_pdfgen(self):
        wiz = self._wizard()
        self.assertFalse(wiz.pdfgen_use_custom)

    def test_wizard_pdfgen_template_id_resolves_when_toggled_on(self):
        self._set_dataset_default_template("99")
        wiz = self._wizard()
        # Toggle is computed on; template should resolve to dataset default.
        self.assertTrue(wiz.pdfgen_use_custom)
        self.assertEqual(wiz.pdfgen_template_id, "99")

    def test_wizard_pdfgen_template_id_blank_when_toggled_off(self):
        wiz = self._wizard()
        wiz.pdfgen_use_custom = False
        wiz.invalidate_recordset(["pdfgen_template_id"])
        self.assertFalse(wiz.pdfgen_template_id)

    def test_wizard_apply_substitution_requires_template(self):
        wiz = self._wizard()
        wiz.pdfgen_use_custom = True
        wiz.pdfgen_template_id = False
        with self.assertRaises(UserError):
            wiz._pdfgen_apply_substitution([])

    def test_wizard_apply_substitution_regenerates_when_template_changed(self):
        # Existing pdfgen attachment with template=old; user picks different one.
        self._attach(description="pdfgen:template:old")
        wiz = self._wizard()
        wiz.pdfgen_use_custom = True
        wiz.pdfgen_template_id = "new"
        client = MagicMock()
        client.generate.return_value = {"response": PDF_B64}
        with patch(
            _BUILD_CLIENT,
            return_value=client,
        ):
            out = wiz._pdfgen_apply_substitution(
                [{"id": "ph", "name": "x.pdf", "mimetype": "application/pdf", "placeholder": True}]
            )
        # Fresh attachment with marker for "new" should be in the output.
        added = [w for w in out if not w.get("placeholder")]
        self.assertEqual(len(added), 1)
        att = self.env["ir.attachment"].browse(added[0]["id"])
        self.assertTrue(att.description.endswith(":new"))

    # ---------------------------------------------------- substitution

    def test_apply_substitution_drops_placeholder_and_adds_pdfgen(self):
        wiz = self._wizard()
        latest = self._attach(description="pdfgen:template:42")
        wiz.pdfgen_use_custom = True
        wiz.pdfgen_template_id = "42"
        widget = [
            {
                "id": "placeholder_invoice.pdf",
                "name": "invoice.pdf",
                "mimetype": "application/pdf",
                "placeholder": True,
            },
            {
                "id": 999,
                "name": "manual.pdf",
                "mimetype": "application/pdf",
                "placeholder": False,
                "manual": True,
            },
        ]
        out = wiz._pdfgen_apply_substitution(widget)
        # Manual entry preserved
        self.assertTrue(any(w.get("manual") for w in out))
        # Placeholder gone
        self.assertFalse(any(w.get("placeholder") for w in out))
        # Pdfgen attachment present
        self.assertTrue(any(w["id"] == latest.id for w in out))

    def test_apply_substitution_strips_existing_standard_report(self):
        wiz = self._wizard()
        pdfgen_att = self._attach(description="pdfgen:template:42")
        wiz.pdfgen_use_custom = True
        wiz.pdfgen_template_id = "42"
        widget = [
            {
                "id": "placeholder_invoice.pdf",
                "name": "invoice.pdf",
                "mimetype": "application/pdf",
                "placeholder": True,
            },
            # Pre-existing standard-report attachment from invoice post.
            {
                "id": 1234,
                "name": "INV-2026-001.pdf",
                "mimetype": "application/pdf",
                "placeholder": False,
                "protect_from_deletion": True,
            },
        ]
        out = wiz._pdfgen_apply_substitution(widget)
        # Only our pdfgen attachment should survive — both the placeholder
        # AND the existing standard report are stripped.
        self.assertEqual([w["id"] for w in out], [pdfgen_att.id])

    def test_apply_substitution_promotes_invoice_pdf_report_id(self):
        wiz = self._wizard()
        pdfgen_att = self._attach(description="pdfgen:template:42")
        wiz.pdfgen_use_custom = True
        wiz.pdfgen_template_id = "42"
        wiz._pdfgen_apply_substitution([])
        self.invoice.invalidate_recordset(["invoice_pdf_report_id"])
        self.assertEqual(self.invoice.invoice_pdf_report_id, pdfgen_att)

    # -------------------------------------- _compute_mail_attachments_widget

    def test_compute_widget_swaps_in_pdfgen_when_toggled_on(self):
        # Create a real (saved) wizard so the compute fires and writes back.
        wiz = (
            self.env["account.move.send.wizard"]
            .with_context(active_ids=[self.invoice.id], default_move_id=self.invoice.id)
            .create({"move_id": self.invoice.id})
        )
        att = self._attach(description="pdfgen:template:42")
        wiz.pdfgen_use_custom = True
        wiz.pdfgen_template_id = "42"
        # Reading the field triggers the recompute via the depends.
        widget = wiz.mail_attachments_widget or []
        self.assertTrue(any(w.get("id") == att.id for w in widget))
        self.assertFalse(any(w.get("placeholder") for w in widget))

    def test_compute_widget_records_user_error_and_disables_toggle(self):
        wiz = (
            self.env["account.move.send.wizard"]
            .with_context(active_ids=[self.invoice.id], default_move_id=self.invoice.id)
            .create({"move_id": self.invoice.id})
        )
        # No pdfgen attachment, no template → _pdfgen_apply_substitution
        # raises UserError (no template picked); the compute should swallow
        # it into pdfgen_error and flip the toggle off.
        wiz.pdfgen_use_custom = True
        wiz.pdfgen_template_id = False
        # Force the compute by reading.
        _ = wiz.mail_attachments_widget
        self.assertFalse(wiz.pdfgen_use_custom)
        self.assertTrue(wiz.pdfgen_error)
