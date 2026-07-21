"""Tests for the generic Send-by-email substitution (`mail.compose.message`
inheriting `pdfgen.send.mixin`), exercised through sale.order — the model
whose Send button goes through the mail composer.
"""

import base64
from unittest.mock import MagicMock, patch

from odoo.addons.sale.tests.common import SaleCommon
from odoo.tests.common import tagged

PDF_B64 = base64.b64encode(b"%PDF-1.4 fake pdfgen pdf").decode()

_BUILD_CLIENT = (
    "odoo.addons.pdfgeneratorapi_connector.models." + "pdfgen_send_mixin.build_pdfgen_client"
)


@tagged("post_install", "-at_install")
class TestMailComposerPdfgen(SaleCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("pdfgen.api_base_url", "https://us1.pdfgeneratorapi.com/api/v4")
        icp.set_param("pdfgen.api_key", "test-key")
        icp.set_param("pdfgen.api_secret", "test-secret")
        icp.set_param("pdfgen.workspace_identifier", "me@example.com")
        cls.dataset = cls.env.ref("pdfgeneratorapi_connector_sale.dataset_sale_order")
        cls.template = cls.env.ref("sale.email_template_edi_sale")

    def setUp(self):
        super().setUp()
        self.env["ir.attachment"].search(
            [
                ("res_model", "=", "sale.order"),
                ("res_id", "=", self.sale_order.id),
                ("description", "=like", "pdfgen:%"),
            ]
        ).unlink()
        self._set_dataset_default_template(False)

    # ------------------------------------------------------------- helpers

    def _set_dataset_default_template(self, tid):
        # The Selection getter is API-backed; write through SQL so tests
        # don't need a live endpoint to pass validation.
        self.env.cr.execute(
            "UPDATE pdfgen_model_dataset SET default_template_id=%s WHERE id=%s",
            (tid or None, self.dataset.id),
        )
        self.dataset.invalidate_recordset()

    def _client(self):
        client = MagicMock()
        client.generate.return_value = {"response": PDF_B64}
        return client

    def _composer(self, template=None, **values):
        """Build a monorecord comment composer the way `Send` does.

        `attachment_ids` is read before returning: the field is on the
        composer form, so the UI always triggers its (lazy) compute — and
        that compute is where the substitution happens.
        """
        template = self.template if template is None else template
        composer = (
            self.env["mail.compose.message"]
            .with_context(
                default_model="sale.order",
                default_res_ids=self.sale_order.ids,
                default_composition_mode="comment",
            )
            .create(
                {
                    "model": "sale.order",
                    "res_ids": repr(self.sale_order.ids),
                    "composition_mode": "comment",
                    "template_id": template.id if template else False,
                    **values,
                }
            )
        )
        _ = composer.attachment_ids  # force the lazy compute
        return composer

    def _pdfgen_attachments(self, composer):
        return composer.attachment_ids.filtered(
            lambda a: a.description and a.description.startswith("pdfgen:")
        )

    def _rendered_reports(self, composer):
        return composer.attachment_ids.filtered(
            lambda a: a.res_model == "mail.compose.message" and not a.res_id
        )

    # --------------------------------------------------------------- tests

    def test_fields_available_on_composer(self):
        self.assertIn("pdfgen_use_custom", self.env["mail.compose.message"]._fields)
        self.assertIn("pdfgen_template_id", self.env["mail.compose.message"]._fields)

    def test_configured_only_for_models_with_a_dataset(self):
        composer = self._composer()
        self.assertTrue(composer.pdfgen_configured)
        partner_composer = (
            self.env["mail.compose.message"]
            .with_context(default_model="res.partner", default_res_ids=self.partner.ids)
            .create(
                {
                    "model": "res.partner",
                    "res_ids": repr(self.partner.ids),
                    "composition_mode": "comment",
                }
            )
        )
        self.assertFalse(partner_composer.pdfgen_configured)

    def test_toggle_defaults_on_when_the_model_has_a_dataset(self):
        """No default template configured: the toggle is still ON (the
        connector is in use for sale.order), the standard report stays put
        and the user is asked to pick a template."""
        composer = self._composer()
        self.assertTrue(composer.pdfgen_use_custom)
        self.assertIn("template", composer.pdfgen_error)
        self.assertTrue(self._rendered_reports(composer), "standard report must be untouched")

    def test_toggle_defaults_off_without_a_dataset(self):
        self.dataset.active = False
        composer = self._composer()
        self.assertFalse(composer.pdfgen_configured)
        self.assertFalse(composer.pdfgen_use_custom)
        self.assertTrue(self._rendered_reports(composer))

    def test_toggle_defaults_off_without_a_mail_template(self):
        """The chatter's plain "Send message" composer renders no report, so
        an email never grows an unexpected PDF."""
        self._set_dataset_default_template("42")
        composer = self._composer(template=False)
        self.assertFalse(composer.pdfgen_use_custom)

    def test_substitutes_standard_report_when_default_template_set(self):
        self._set_dataset_default_template("42")
        client = self._client()
        with patch(_BUILD_CLIENT, return_value=client):
            composer = self._composer()
        self.assertTrue(composer.pdfgen_use_custom)
        self.assertEqual(composer.pdfgen_template_id, "42")
        self.assertFalse(composer.pdfgen_error)
        client.generate.assert_called_once()
        self.assertFalse(self._rendered_reports(composer), "the standard report must be dropped")
        pdfgen_atts = self._pdfgen_attachments(composer)
        self.assertEqual(len(pdfgen_atts), 1)
        self.assertEqual(pdfgen_atts.description, "pdfgen:template:42")
        self.assertEqual(pdfgen_atts.res_model, "sale.order")
        self.assertEqual(pdfgen_atts.res_id, self.sale_order.id)

    def test_reuses_an_existing_pdfgen_attachment(self):
        self._set_dataset_default_template("42")
        existing = self.env["ir.attachment"].create(
            {
                "name": "already-there.pdf",
                "type": "binary",
                "datas": PDF_B64,
                "res_model": "sale.order",
                "res_id": self.sale_order.id,
                "mimetype": "application/pdf",
                "description": "pdfgen:template:42",
            }
        )
        client = self._client()
        with patch(_BUILD_CLIENT, return_value=client):
            composer = self._composer()
        client.generate.assert_not_called()
        self.assertIn(existing, composer.attachment_ids)

    def test_toggling_off_restores_the_standard_report(self):
        self._set_dataset_default_template("42")
        with patch(_BUILD_CLIENT, return_value=self._client()):
            composer = self._composer()
            self.assertTrue(self._pdfgen_attachments(composer))
            composer.pdfgen_use_custom = False
            _ = composer.attachment_ids
        self.assertFalse(self._pdfgen_attachments(composer))
        self.assertTrue(self._rendered_reports(composer), "the report is rendered again")

    def test_picking_a_template_by_hand(self):
        composer = self._composer()
        with patch(_BUILD_CLIENT, return_value=self._client()):
            composer.pdfgen_template_id = "77"
            _ = composer.attachment_ids
        self.assertFalse(composer.pdfgen_error)
        self.assertEqual(self._pdfgen_attachments(composer).description, "pdfgen:template:77")
        self.assertFalse(self._rendered_reports(composer))

    def test_no_template_prompts_instead_of_erroring(self):
        composer = self._composer()
        self.assertTrue(composer.pdfgen_use_custom, "the toggle must stay on")
        self.assertIn("template", composer.pdfgen_error)
        self.assertTrue(self._rendered_reports(composer), "standard report stays attached")

    def test_generation_failure_falls_back_to_the_standard_report(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import PdfGenApiError

        self._set_dataset_default_template("42")
        client = MagicMock()
        client.generate.side_effect = PdfGenApiError(500, "nope")
        with patch(_BUILD_CLIENT, return_value=client):
            composer = self._composer()
        self.assertFalse(composer.pdfgen_use_custom)
        self.assertIn("500", composer.pdfgen_error)
        self.assertTrue(self._rendered_reports(composer), "standard report stays attached")

    def test_manual_uploads_survive_the_substitution(self):
        self._set_dataset_default_template("42")
        manual = self.env["ir.attachment"].create(
            {
                "name": "hand-picked.pdf",
                "type": "binary",
                "datas": PDF_B64,
                # The composer's upload widget stores files on the document's
                # own thread, never on mail.compose.message.
                "res_model": "sale.order",
                "res_id": self.sale_order.id,
                "mimetype": "application/pdf",
            }
        )
        composer = self._composer()
        composer.attachment_ids |= manual
        with patch(_BUILD_CLIENT, return_value=self._client()):
            composer.pdfgen_template_id = "42"
            _ = composer.attachment_ids
        self.assertIn(manual, composer.attachment_ids)
        # …and through a round trip back to the standard report.
        composer.pdfgen_use_custom = False
        _ = composer.attachment_ids
        self.assertIn(manual, composer.attachment_ids)

    def test_mass_mail_mode_is_left_alone(self):
        self._set_dataset_default_template("42")
        composer = self._composer(composition_mode="mass_mail")
        self.assertFalse(composer.pdfgen_configured)
        self.assertFalse(composer.pdfgen_use_custom)

    def test_send_posts_the_pdfgen_attachment(self):
        self._set_dataset_default_template("42")
        with patch(_BUILD_CLIENT, return_value=self._client()):
            composer = self._composer()
            composer.action_send_mail()
        message = self.sale_order.message_ids[0]
        names = message.attachment_ids.mapped("description")
        self.assertIn("pdfgen:template:42", names)

    def test_multi_record_composer_is_left_alone(self):
        """Two records in one composer: there is no single document to render
        a PDF for, so the panel stays out of the way."""
        other = self.sale_order.copy()
        composer = self._composer(res_ids=repr((self.sale_order | other).ids))
        self.assertFalse(composer.pdfgen_configured)
        self.assertFalse(composer._pdfgen_target_record())

    def test_unknown_model_is_left_alone(self):
        composer = self._composer()
        composer.model = "no.such.model"
        self.assertFalse(composer._pdfgen_target_record())

    def test_preview_renders_when_a_template_is_picked(self):
        self._set_dataset_default_template("42")
        client = self._client()
        with patch(_BUILD_CLIENT, return_value=client):
            composer = self._composer()
            # The generated attachment matches the template, so the preview
            # embeds it instead of calling the API again.
            self.assertIn("<iframe", composer.pdfgen_preview_html)

    def test_preview_is_blank_when_the_toggle_is_off(self):
        composer = self._composer()
        composer.pdfgen_use_custom = False
        self.assertFalse(composer.pdfgen_preview_html)
        self.assertFalse(composer.pdfgen_template_id)

    def test_template_selection_is_shared_with_the_dataset_field(self):
        with patch.object(
            type(self.env["pdfgen.model.dataset"]),
            "_selection_default_template_id",
            return_value=[("42", "Quote")],
        ):
            self.assertEqual(
                self.env["mail.compose.message"]._selection_pdfgen_template_id(),
                [("42", "Quote")],
            )

    def test_toggling_off_keeps_a_report_that_is_still_attached(self):
        """Turning the toggle off when the standard report was never dropped
        (no template was ever picked) leaves the attachment set alone."""
        composer = self._composer()
        before = composer.attachment_ids
        composer.pdfgen_use_custom = False
        self.assertEqual(composer.attachment_ids, before)
