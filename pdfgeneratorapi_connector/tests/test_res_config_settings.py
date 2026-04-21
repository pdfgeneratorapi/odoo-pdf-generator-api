from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestResConfigSettings(TransactionCase):
    def _make_config(self, **overrides):
        vals = {
            "pdfgen_api_base_url": "https://us1.pdfgeneratorapi.com/api/v4",
            "pdfgen_api_key": "test-key",
            "pdfgen_api_secret": "test-secret",
            "pdfgen_workspace_identifier": "me@example.com",
        }
        vals.update(overrides)
        return self.env["res.config.settings"].create(vals)

    def test_get_client_raises_when_credentials_missing(self):
        config = self._make_config(
            pdfgen_api_key=False,
            pdfgen_api_secret=False,
            pdfgen_workspace_identifier=False,
        )
        with self.assertRaises(UserError) as ctx:
            config._get_pdfgen_client()
        msg = str(ctx.exception)
        self.assertIn("API Key", msg)
        self.assertIn("API Secret", msg)
        self.assertIn("Workspace Identifier", msg)

    def test_get_client_returns_configured_instance(self):
        config = self._make_config()
        client = config._get_pdfgen_client()
        self.assertEqual(client.api_key, "test-key")
        self.assertEqual(client.api_secret, "test-secret")
        self.assertEqual(client.workspace, "me@example.com")

    def test_test_connection_success_returns_notification(self):
        config = self._make_config()
        fake_client = MagicMock()
        fake_client.ping.return_value = {"response": {"name": "My Workspace"}}
        with patch.object(type(config), "_get_pdfgen_client", return_value=fake_client):
            result = config.action_pdfgen_test_connection()
        self.assertEqual(result["tag"], "display_notification")
        self.assertEqual(result["params"]["type"], "success")
        self.assertIn("My Workspace", result["params"]["message"])

    def test_test_connection_failure_raises_user_error(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_api_client import (
            PdfGenApiError,
        )

        config = self._make_config()
        fake_client = MagicMock()
        fake_client.ping.side_effect = PdfGenApiError(401, '{"message":"bad"}')
        with (
            patch.object(type(config), "_get_pdfgen_client", return_value=fake_client),
            self.assertRaises(UserError) as ctx,
        ):
            config.action_pdfgen_test_connection()
        self.assertIn("401", str(ctx.exception))
