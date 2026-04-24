import base64
from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

PREVIEW_HTML = "<html><body><h1>Hello {{name}}</h1></body></html>"
PREVIEW_B64 = base64.b64encode(PREVIEW_HTML.encode()).decode()


@tagged("post_install", "-at_install")
class TestCoverageWizard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("pdfgen.api_base_url", "https://us1.pdfgeneratorapi.com/api/v4")
        icp.set_param("pdfgen.api_key", "test-key")
        icp.set_param("pdfgen.api_secret", "test-secret")
        icp.set_param("pdfgen.workspace_identifier", "me@example.com")
        cls.partner_model = cls.env.ref("base.model_res_partner")
        cls.dataset = cls.env["pdfgen.model.dataset"].create(
            {
                "name": "Partner test dataset",
                "model_id": cls.partner_model.id,
            }
        )
        cls.env["pdfgen.model.dataset.line"].create(
            [
                {
                    "dataset_id": cls.dataset.id,
                    "placeholder_path": "name",
                    "odoo_field_path": "name",
                },
                {
                    "dataset_id": cls.dataset.id,
                    "placeholder_path": "vat",
                    "odoo_field_path": "vat",
                },
            ]
        )
        list_row = cls.env["pdfgen.model.dataset.line"].create(
            {
                "dataset_id": cls.dataset.id,
                "placeholder_path": "children",
                "is_list": True,
                "odoo_field_path": "child_ids",
            }
        )
        cls.env["pdfgen.model.dataset.line"].create(
            {
                "dataset_id": cls.dataset.id,
                "parent_id": list_row.id,
                "placeholder_path": "name",
                "odoo_field_path": "name",
            }
        )

    def _patch_client(self, client):
        return patch.object(
            self.env["pdfgen.coverage.wizard"].__class__,
            "_build_client",
            return_value=client,
        )

    def _new_wizard(self, template_id="42"):
        return self.env["pdfgen.coverage.wizard"].create(
            {"dataset_id": self.dataset.id, "template_id": template_id}
        )

    def test_all_covered_when_template_matches_dataset(self):
        wizard = self._new_wizard(template_id="100")
        client = MagicMock()
        client.get_template_data.return_value = {
            "response": {
                "name": "",
                "vat": "",
                "children": [{"name": ""}],
            }
        }
        client._request.return_value = {"response": {"name": "Nice template"}}
        with self._patch_client(client):
            wizard.action_check()
        self.assertTrue(wizard.checked)
        self.assertEqual(wizard.coverage_total, 3)
        self.assertEqual(wizard.coverage_matched, 3)
        self.assertFalse(wizard.missing_placeholders)
        self.assertFalse(wizard.extra_placeholders)
        self.assertEqual(wizard.template_name, "Nice template")

    def test_missing_placeholders_surface_when_template_needs_more(self):
        wizard = self._new_wizard(template_id="missing")
        client = MagicMock()
        client.get_template_data.return_value = {
            "response": {
                "name": "",
                "vat": "",
                "email": "",
                "phone": "",
                "children": [{"name": "", "age": ""}],
            }
        }
        client._request.side_effect = Exception("skip detail — not a PdfGenApiError")
        # Detail-fetch error is only caught for PdfGenApiError; make it a hit.
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client._request.side_effect = PdfGenApiError(500, "skip")
        with self._patch_client(client):
            wizard.action_check()
        missing = set(wizard.missing_placeholders.split("\n"))
        self.assertEqual(missing, {"email", "phone", "children[].age"})
        self.assertFalse(wizard.extra_placeholders)

    def test_extra_placeholders_surface_when_dataset_maps_unused(self):
        wizard = self._new_wizard(template_id="extras")
        client = MagicMock()
        client.get_template_data.return_value = {"response": {"name": ""}}
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client._request.side_effect = PdfGenApiError(500, "skip")
        with self._patch_client(client):
            wizard.action_check()
        extra = set(wizard.extra_placeholders.split("\n"))
        self.assertIn("vat", extra)
        self.assertIn("children[].name", extra)
        self.assertFalse(wizard.missing_placeholders)

    def test_api_error_wrapped_as_user_error(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        wizard = self._new_wizard(template_id="boom")
        client = MagicMock()
        client.get_template_data.side_effect = PdfGenApiError(404, "template not found")
        with self._patch_client(client), self.assertRaises(UserError) as ctx:
            wizard.action_check()
        self.assertIn("404", str(ctx.exception))

    def test_selection_template_id_live_list(self):
        client = MagicMock()
        client.list_templates.return_value = {
            "response": [
                {"id": 1, "name": "Invoice"},
                {"id": 2, "name": "Quote"},
            ],
        }
        with self._patch_client(client):
            selection = self.env["pdfgen.coverage.wizard"]._selection_template_id()
        self.assertEqual(selection, [("1", "Invoice"), ("2", "Quote")])

    def test_selection_template_id_returns_empty_when_unconfigured(self):
        icp = self.env["ir.config_parameter"].sudo()
        for key in ("pdfgen.api_key", "pdfgen.api_secret", "pdfgen.workspace_identifier"):
            icp.set_param(key, "")
        # Phase F: company-level creds win — wipe those too.
        self.env.company.write(
            {
                "pdfgen_api_key": False,
                "pdfgen_api_secret": False,
                "pdfgen_workspace_identifier": False,
            }
        )
        selection = self.env["pdfgen.coverage.wizard"]._selection_template_id()
        self.assertEqual(selection, [])

    def test_selection_template_id_skips_entries_without_id(self):
        client = MagicMock()
        client.list_templates.return_value = {
            "response": [
                {"id": 1, "name": "Valid"},
                {"name": "Missing id"},
                {"id": 2},
            ],
        }
        with self._patch_client(client):
            selection = self.env["pdfgen.coverage.wizard"]._selection_template_id()
        self.assertEqual(selection, [("1", "Valid"), ("2", "Template 2")])

    def test_selection_template_id_handles_non_list_response(self):
        client = MagicMock()
        client.list_templates.return_value = {"response": "oops"}
        with self._patch_client(client):
            selection = self.env["pdfgen.coverage.wizard"]._selection_template_id()
        self.assertEqual(selection, [])

    def test_selection_template_id_swallows_api_errors(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client = MagicMock()
        client.list_templates.side_effect = PdfGenApiError(503, "unavailable")
        with self._patch_client(client):
            selection = self.env["pdfgen.coverage.wizard"]._selection_template_id()
        self.assertEqual(selection, [])

    def test_action_check_errors_when_unconfigured(self):
        icp = self.env["ir.config_parameter"].sudo()
        for key in ("pdfgen.api_key", "pdfgen.api_secret", "pdfgen.workspace_identifier"):
            icp.set_param(key, "")
        # Per-company creds now win over ICP (multi-company feature). Wipe
        # those too so the client-build sees a genuinely unconfigured env.
        self.env.company.write(
            {
                "pdfgen_api_key": False,
                "pdfgen_api_secret": False,
                "pdfgen_workspace_identifier": False,
            }
        )
        wizard = self._new_wizard(template_id="99")
        with self.assertRaises(UserError) as ctx:
            wizard.action_check()
        self.assertIn("configured", str(ctx.exception).lower())

    def test_empty_list_response_is_treated_as_no_placeholders(self):
        """A blank template returns `{"response": []}`; should be coverage 0/0."""
        wizard = self._new_wizard(template_id="blank")
        client = MagicMock()
        client.get_template_data.return_value = {"response": []}
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        client._request.side_effect = PdfGenApiError(500, "skip")
        with self._patch_client(client):
            wizard.action_check()
        self.assertEqual(wizard.coverage_total, 0)
        self.assertEqual(wizard.coverage_matched, 0)
        self.assertFalse(wizard.missing_placeholders)
        # Dataset lines all become "extras" — they're all unused by this empty template.
        self.assertTrue(wizard.extra_placeholders)

    def test_preview_uses_real_record_when_available(self):
        partner = self.env["res.partner"].create({"name": "Preview Target"})
        wizard = self._new_wizard(template_id="77")
        client = MagicMock()
        client.generate.return_value = {"response": PREVIEW_B64}
        # Force the "first partner" search to return our canary record so the
        # assertions below are deterministic even in a DB with admin/OdooBot/etc.
        with (
            self._patch_client(client),
            patch.object(type(partner), "search", return_value=partner),
        ):
            wizard.action_preview()
        call = client.generate.call_args
        self.assertEqual(call.kwargs["fmt"], "html")
        self.assertEqual(call.kwargs["output"], "base64")
        self.assertEqual(call.kwargs["data"]["name"], "Preview Target")
        client.get_template_data.assert_not_called()
        self.assertEqual(wizard.preview_html, PREVIEW_HTML)
        self.assertIn("Preview Target", wizard.preview_source or "")

    def test_preview_falls_back_to_api_sample_when_no_record(self):
        wizard = self._new_wizard(template_id="77")
        client = MagicMock()
        client.get_template_data.return_value = {"response": {"name": "SAMPLE"}}
        client.generate.return_value = {"response": PREVIEW_B64}
        empty = self.env["res.partner"]
        # Patching res.partner.search for the duration of the action forces the
        # "no record" branch in _preview_payload without needing to delete real
        # partners (which breaks FKs from res.users, res.company, etc.).
        with (
            self._patch_client(client),
            patch.object(type(empty), "search", return_value=empty),
        ):
            wizard.action_preview()
        client.get_template_data.assert_called_once_with("77")
        self.assertEqual(client.generate.call_args.kwargs["data"], {"name": "SAMPLE"})
        self.assertEqual(wizard.preview_html, PREVIEW_HTML)

    def test_preview_api_error_wrapped_as_user_error(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        self.env["res.partner"].create({"name": "Present"})
        wizard = self._new_wizard(template_id="boom")
        client = MagicMock()
        client.generate.side_effect = PdfGenApiError(502, "upstream down")
        with self._patch_client(client), self.assertRaises(UserError) as ctx:
            wizard.action_preview()
        self.assertIn("502", str(ctx.exception))

    def test_preview_invalid_base64_wrapped_as_user_error(self):
        self.env["res.partner"].create({"name": "Present"})
        wizard = self._new_wizard(template_id="77")
        client = MagicMock()
        # Non-multiple-of-4 → binascii.Error (a ValueError subclass) → UserError.
        client.generate.return_value = {"response": "abc"}
        with self._patch_client(client), self.assertRaises(UserError) as ctx:
            wizard.action_preview()
        self.assertIn("base64", str(ctx.exception).lower())

    def test_preview_unexpected_response_shape_raises(self):
        self.env["res.partner"].create({"name": "Present"})
        wizard = self._new_wizard(template_id="77")
        client = MagicMock()
        client.generate.return_value = 42  # not a dict, not a string
        with self._patch_client(client), self.assertRaises(UserError) as ctx:
            wizard.action_preview()
        self.assertIn("Unexpected", str(ctx.exception))

    def test_preview_falls_back_when_resolve_payload_raises(self):
        wizard = self._new_wizard(template_id="77")
        client = MagicMock()
        client.get_template_data.return_value = {"response": {"name": "SAMPLE"}}
        client.generate.return_value = {"response": PREVIEW_B64}
        # resolve_payload blows up → wizard should log & fall back to API sample.
        with (
            self._patch_client(client),
            patch.object(
                type(self.dataset),
                "resolve_payload",
                side_effect=RuntimeError("boom"),
            ),
        ):
            wizard.action_preview()
        client.get_template_data.assert_called_once()
        self.assertEqual(wizard.preview_html, PREVIEW_HTML)

    def test_preview_handles_non_dict_sample_data_response(self):
        wizard = self._new_wizard(template_id="77")
        client = MagicMock()
        client.get_template_data.return_value = {"response": "not-a-dict"}
        client.generate.return_value = {"response": PREVIEW_B64}
        empty = self.env["res.partner"]
        with (
            self._patch_client(client),
            patch.object(type(empty), "search", return_value=empty),
        ):
            wizard.action_preview()
        # Empty dict fallback means generate got {} as data.
        self.assertEqual(client.generate.call_args.kwargs["data"], {})

    def test_extract_payload_handles_various_shapes(self):
        Wiz = self.env["pdfgen.coverage.wizard"].__class__
        # String response → returned as-is.
        self.assertEqual(Wiz._extract_payload("abc="), "abc=")
        # Non-dict, non-string → None.
        self.assertIsNone(Wiz._extract_payload(42))
        # Nested dict under 'response' with a 'base64' string.
        self.assertEqual(Wiz._extract_payload({"response": {"base64": "xyz"}}), "xyz")
        # Nested dict under 'data' with 'content' wrapper.
        self.assertEqual(Wiz._extract_payload({"data": {"content": "deadbeef"}}), "deadbeef")
        # Neither 'response' nor 'data' → None.
        self.assertIsNone(Wiz._extract_payload({"foo": "bar"}))

    def test_preview_sample_data_error_wrapped(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        wizard = self._new_wizard(template_id="77")
        client = MagicMock()
        client.get_template_data.side_effect = PdfGenApiError(404, "missing")
        empty = self.env["res.partner"]
        with (
            self._patch_client(client),
            patch.object(type(empty), "search", return_value=empty),
            self.assertRaises(UserError) as ctx,
        ):
            wizard.action_preview()
        self.assertIn("404", str(ctx.exception))
