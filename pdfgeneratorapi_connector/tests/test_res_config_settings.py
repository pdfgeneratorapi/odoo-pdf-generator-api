from unittest.mock import MagicMock, patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestResConfigSettings(TransactionCase):
    def _make_config(self, **overrides):
        # Clear ir.config_parameter values for a deterministic starting point;
        # config fields that read from ICP otherwise inherit whatever the
        # developer set via the UI in this DB.
        icp = self.env["ir.config_parameter"].sudo()
        for key in ("pdfgen.editor_web_url",):
            icp.set_param(key, "")
        vals = {
            "pdfgen_api_base_url": "https://us1.pdfgeneratorapi.com/api/v4",
            "pdfgen_api_key": "test-key",
            "pdfgen_api_secret": "test-secret",
            "pdfgen_workspace_identifier": "me@example.com",
            "pdfgen_editor_web_url": False,
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
        # editor_web_url empty by default — client falls back to stripping
        # /api/vN from base_url.
        self.assertIsNone(client.editor_web_url)

    def test_get_client_forwards_editor_web_url_override(self):
        config = self._make_config()
        config.pdfgen_editor_web_url = "http://localhost:8080"
        config.execute()
        client = config._get_pdfgen_client()
        self.assertEqual(client.editor_web_url, "http://localhost:8080")

    def test_module_version_matches_installed_module(self):
        """Settings surface the installed connector version for support triage."""
        config = self._make_config()
        module = (
            self.env["ir.module.module"]
            .sudo()
            .search([("name", "=", "pdfgeneratorapi_connector")], limit=1)
        )
        self.assertTrue(config.pdfgen_module_version)
        self.assertTrue(config.pdfgen_module_version.startswith(module.latest_version))

    def test_module_version_reports_installed_bridges(self):
        """The core version alone can't answer "is this deployment current?" —
        bridges carry their own manifest versions, so a fix living entirely in
        a bridge moves no core digit. Each installed bridge is listed, with the
        series prefix the core version already states stripped off.
        """
        Module = self.env["ir.module.module"].sudo()
        core = Module.search([("name", "=", "pdfgeneratorapi_connector")], limit=1)
        bridges = Module.search(
            [
                ("name", "=like", "pdfgeneratorapi_connector_%"),
                ("state", "=", "installed"),
            ]
        )
        self.assertTrue(bridges, "install at least one bridge for this test to mean anything")

        value = self._make_config().pdfgen_module_version
        self.assertTrue(value.startswith(core.latest_version))
        series = ".".join(core.latest_version.split(".")[:2]) + "."
        for bridge in bridges:
            label = bridge.name[len("pdfgeneratorapi_connector_") :]
            short = bridge.latest_version.removeprefix(series)
            self.assertIn(f"{label} {short}", value)
        # The series is stated once by the core version, not repeated per bridge.
        self.assertEqual(value.count(series), 1)

    def test_module_version_lists_only_installed_bridges(self):
        """An uninstalled bridge must not be advertised as running."""
        Module = self.env["ir.module.module"].sudo()
        uninstalled = Module.search(
            [
                ("name", "=like", "pdfgeneratorapi_connector_%"),
                ("state", "!=", "installed"),
            ]
        )
        value = self._make_config().pdfgen_module_version
        for bridge in uninstalled:
            label = bridge.name[len("pdfgeneratorapi_connector_") :]
            self.assertNotIn(f"{label} ", value)

    def test_module_version_reaches_the_web_client(self):
        """Guard the client path, not just attribute access.

        `config.pdfgen_module_version` always computes on a cache miss, so the
        test above can never catch this class of bug. The web client opens
        Settings as a *new* record and fills the form from default_get()/
        onchange(); Odoo <= 18 stamps False into the cache for every
        fields_spec entry default_get does not supply, so a dependency-less
        computed field reached the browser as False and the footer was blank.
        """
        Settings = self.env["res.config.settings"]
        core_version = (
            self.env["ir.module.module"]
            .sudo()
            .search([("name", "=", "pdfgeneratorapi_connector")], limit=1)
            .latest_version
        )
        self.assertTrue(core_version, "module must be installed for this test to mean anything")
        expected = Settings._pdfgen_installed_version()
        self.assertTrue(expected.startswith(core_version))

        # default_get is the version-agnostic guard: no Odoo version returns a
        # computed field from it, so this fails on 17, 18 and 19 alike if the
        # field ever regresses to `compute=`.
        defaults = Settings.default_get(["pdfgen_module_version"])
        self.assertEqual(defaults.get("pdfgen_module_version"), expected)

        # onchange is literally what the browser calls when opening Settings.
        # This is the assertion that reproduced the blank footer on 17/18.
        result = Settings.onchange({}, [], {"company_id": {}, "pdfgen_module_version": {}})
        self.assertEqual(result["value"]["pdfgen_module_version"], expected)

    def test_test_connection_success_returns_notification(self):
        config = self._make_config()
        fake_client = MagicMock()
        fake_client.ping.return_value = {"response": [], "meta": {"total": 0}}
        with patch.object(type(config), "_get_pdfgen_client", return_value=fake_client):
            result = config.action_pdfgen_test_connection()
        self.assertEqual(result["tag"], "display_notification")
        self.assertEqual(result["params"]["type"], "success")
        self.assertIn("me@example.com", result["params"]["message"])

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

    def test_bridge_module_toggle_marks_module_for_install(self):
        """`module_<name>` toggle wires through to Odoo's install machinery.

        Asserting one bridge is enough — `res.config.settings.execute()` uses
        the same prefix scan for every `module_*` field, so proving the
        plumbing works for one toggle proves it for all five.
        """
        modules = self.env["ir.module.module"].search(
            [("name", "=", "pdfgeneratorapi_connector_sale")]
        )
        # Start from a known state: if a previous test left the bridge marked
        # for install, the test-run DB still reports a non-uninstalled state;
        # short-circuit so the assertion stays meaningful.
        if modules and modules.state not in ("uninstalled", "uninstallable"):
            self.skipTest("Sales bridge already installed in this test DB")
        config = self._make_config()
        config.module_pdfgeneratorapi_connector_sale = True
        config.execute()
        module = self.env["ir.module.module"].search(
            [("name", "=", "pdfgeneratorapi_connector_sale")]
        )
        self.assertTrue(module, "Sales bridge module record not found")
        self.assertIn(module.state, ("installed", "to install"))
