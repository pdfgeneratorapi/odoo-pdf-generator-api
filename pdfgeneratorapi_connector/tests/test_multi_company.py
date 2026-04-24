from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMultiCompany(TransactionCase):
    """Per-company pdfgen credentials — company-level value wins, ICP falls back."""

    def _env_for_company(self, company):
        """Odoo 19 dropped `Environment.with_company`; use the context-based
        equivalent, which is what `env.company` reads anyway."""
        ctx = dict(self.env.context, allowed_company_ids=[company.id])
        return self.env(context=ctx)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        # Global defaults. A company that leaves its fields blank inherits these.
        icp.set_param("pdfgen.api_base_url", "https://global.pdfgen.test/api/v4")
        icp.set_param("pdfgen.api_key", "global-key")
        icp.set_param("pdfgen.api_secret", "global-secret")
        icp.set_param("pdfgen.workspace_identifier", "global@pdfgen.test")
        icp.set_param("pdfgen.editor_web_url", "")

        # Reuse the existing main company as "fallback" (no overrides set);
        # create ONE fresh company with explicit overrides. Creating two
        # fresh companies from scratch in Odoo 19 triggers CoA bootstrap
        # which can deadlock with the long-lived parallel Odoo worker that
        # serves this same DB, so keep it minimal.
        cls.company_a = cls.env.company  # inherits ICP defaults (no overrides)
        # Clear any stray pdfgen_* values on the main company so the test
        # is deterministic — upgrades can have copied ICP onto it.
        cls.company_a.write(
            {
                "pdfgen_api_base_url": False,
                "pdfgen_api_key": False,
                "pdfgen_api_secret": False,
                "pdfgen_workspace_identifier": False,
                "pdfgen_editor_web_url": False,
            }
        )
        cls.company_b = cls.env["res.company"].create(
            {
                "name": "pdfgen Beta Test Co",
                "pdfgen_api_base_url": "https://beta.pdfgen.test/api/v4",
                "pdfgen_api_key": "beta-key",
                "pdfgen_api_secret": "beta-secret",
                "pdfgen_workspace_identifier": "beta@pdfgen.test",
                "pdfgen_editor_web_url": "https://beta.editor.local",
            }
        )

    def test_company_without_overrides_falls_back_to_icp(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_document_mixin import (
            build_pdfgen_client,
            pdfgen_config,
        )

        env = self._env_for_company(self.company_a)
        self.assertEqual(pdfgen_config(env, "api_key"), "global-key")
        self.assertEqual(pdfgen_config(env, "api_base_url"), "https://global.pdfgen.test/api/v4")
        client = build_pdfgen_client(env)
        self.assertEqual(client.api_key, "global-key")
        self.assertEqual(client.workspace, "global@pdfgen.test")

    def test_company_with_overrides_wins_over_icp(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_document_mixin import (
            build_pdfgen_client,
            pdfgen_config,
        )

        env = self._env_for_company(self.company_b)
        self.assertEqual(pdfgen_config(env, "api_key"), "beta-key")
        self.assertEqual(pdfgen_config(env, "editor_web_url"), "https://beta.editor.local")
        client = build_pdfgen_client(env)
        self.assertEqual(client.api_key, "beta-key")
        self.assertEqual(client.workspace, "beta@pdfgen.test")
        self.assertEqual(client.editor_web_url, "https://beta.editor.local")

    def test_switching_company_switches_client_credentials(self):
        """Same env, different env.company → different creds."""
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_document_mixin import (
            build_pdfgen_client,
        )

        env_a = self._env_for_company(self.company_a)
        env_b = self._env_for_company(self.company_b)
        self.assertEqual(build_pdfgen_client(env_a).api_key, "global-key")
        self.assertEqual(build_pdfgen_client(env_b).api_key, "beta-key")

    def test_missing_credentials_raises(self):
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_document_mixin import (
            build_pdfgen_client,
        )
        from odoo.exceptions import UserError

        # Clear both the company-specific and global creds.
        self.company_a.write(
            {
                "pdfgen_api_key": False,
                "pdfgen_api_secret": False,
                "pdfgen_workspace_identifier": False,
            }
        )
        icp = self.env["ir.config_parameter"].sudo()
        for key in ("pdfgen.api_key", "pdfgen.api_secret", "pdfgen.workspace_identifier"):
            icp.set_param(key, "")
        env = self._env_for_company(self.company_a)
        with self.assertRaises(UserError) as ctx:
            build_pdfgen_client(env)
        self.assertIn("configured", str(ctx.exception).lower())

    def test_pdfgen_configured_reflects_company(self):
        """The compute on pdfgen.document.mixin should flip per company.

        Uses res.partner (which also inherits pdfgen.document.mixin? no —
        doesn't). We just hit the helper directly; no need to materialise a
        record because the compute delegates to the same pdfgen_config().
        """
        from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_document_mixin import (
            pdfgen_config,
        )

        # company_b has explicit creds — config resolves to beta-*.
        env_b = self._env_for_company(self.company_b)
        self.assertTrue(pdfgen_config(env_b, "api_key"))

        # Wipe company_a's overrides AND the global ICP so both layers are blank.
        icp = self.env["ir.config_parameter"].sudo()
        for key in ("pdfgen.api_key", "pdfgen.api_secret", "pdfgen.workspace_identifier"):
            icp.set_param(key, "")
        env_a = self._env_for_company(self.company_a)
        self.assertIsNone(pdfgen_config(env_a, "api_key"))
        self.assertIsNone(pdfgen_config(env_a, "workspace_identifier"))
