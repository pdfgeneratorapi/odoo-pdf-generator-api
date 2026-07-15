from unittest.mock import MagicMock

from odoo.addons.pdfgeneratorapi_connector.models.pdfgen_document_mixin import (
    pdfgen_resolve_template_id,
)
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLibraryTemplateResolution(TransactionCase):
    """Library ("Default") templates are blueprints, not workspace entities.

    `/templates/library/<publicId>` returns a definition with no id and
    `/documents/generate` answers 404 "Entity not found" for a public id, so
    every generate path has to copy the definition into the account first and
    then act on the numeric id of that copy.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        icp = cls.env["ir.config_parameter"].sudo()
        icp.set_param("pdfgen.api_base_url", "https://us1.pdfgeneratorapi.com/api/v4")
        icp.set_param("pdfgen.api_key", "test-key")
        icp.set_param("pdfgen.api_secret", "test-secret")
        icp.set_param("pdfgen.workspace_identifier", "me@example.com")

    def _client(self, new_id=555):
        client = MagicMock()
        client.get_library_template.return_value = {"response": {"name": "Invoice (red)"}}
        client.create_template.return_value = {"response": {"id": new_id}}
        return client

    def test_account_template_id_passes_through_untouched(self):
        client = self._client()
        self.assertEqual(pdfgen_resolve_template_id(self.env, client, "42"), "42")
        client.get_library_template.assert_not_called()
        client.create_template.assert_not_called()

    def test_library_template_is_copied_into_the_account(self):
        client = self._client(new_id=555)
        resolved = pdfgen_resolve_template_id(self.env, client, "lib:invoice-red")
        self.assertEqual(resolved, 555)
        client.get_library_template.assert_called_once_with("invoice-red")
        client.create_template.assert_called_once_with(
            definition={"name": "Invoice (red)"},
        )

    def test_second_use_reuses_the_copy(self):
        """The whole point of caching: generating twice from the same Default
        Template must not leave two templates behind in the workspace."""
        client = self._client(new_id=555)
        first = pdfgen_resolve_template_id(self.env, client, "lib:invoice-red")
        second = pdfgen_resolve_template_id(self.env, client, "lib:invoice-red")
        self.assertEqual((first, second), (555, 555))
        client.create_template.assert_called_once()

    def test_copy_is_cached_per_workspace(self):
        """The copy exists inside one pdfgen workspace — a company pointed at a
        different workspace must make its own, not reuse a foreign id."""
        client = self._client(new_id=555)
        pdfgen_resolve_template_id(self.env, client, "lib:invoice-red")
        self.env["ir.config_parameter"].sudo().set_param(
            "pdfgen.workspace_identifier", "other@example.com"
        )
        client.create_template.return_value = {"response": {"id": 777}}
        self.assertEqual(pdfgen_resolve_template_id(self.env, client, "lib:invoice-red"), 777)
        self.assertEqual(client.create_template.call_count, 2)
