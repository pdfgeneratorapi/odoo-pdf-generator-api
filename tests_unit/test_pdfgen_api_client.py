"""Pure-Python unit tests for the API client.

No Odoo dependency — run via pytest on the host (`uv run pytest`).
"""

import base64
import hashlib
import hmac
import importlib.util
import json
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_ADDON = Path(__file__).parent.parent / "pdfgeneratorapi_connector"


def _load(qualname, relpath):
    spec = importlib.util.spec_from_file_location(qualname, _ADDON / relpath)
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualname] = module
    spec.loader.exec_module(module)
    return module


# pdfgen_api_client does `from ..enums import Format, Output`. Load it under its
# real package name — with the enums module + stub parent packages in sys.modules —
# so that relative import resolves WITHOUT running the addon __init__ (it imports Odoo).
for _pkg in ("pdfgeneratorapi_connector", "pdfgeneratorapi_connector.models"):
    sys.modules.setdefault(_pkg, types.ModuleType(_pkg))
_load("pdfgeneratorapi_connector.enums", "enums.py")

_client_module = _load(
    "pdfgeneratorapi_connector.models.pdfgen_api_client", "models/pdfgen_api_client.py"
)
sys.modules["pdfgen_api_client"] = _client_module  # legacy alias for any string-based patch

PdfGenApiClient = _client_module.PdfGenApiClient
PdfGenApiError = _client_module.PdfGenApiError


def _b64url_decode(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded.encode())


class JwtMintingTests(unittest.TestCase):
    def setUp(self):
        self.client = PdfGenApiClient(
            base_url="https://example.test/api/v4",
            api_key="key-123",
            api_secret="secret-456",
            workspace_identifier="me@example.com",
        )

    def test_jwt_has_three_segments(self):
        token = self.client._jwt()
        self.assertEqual(token.count("."), 2)

    def test_header_is_hs256(self):
        header_seg = self.client._jwt().split(".")[0]
        header = json.loads(_b64url_decode(header_seg))
        self.assertEqual(header, {"alg": "HS256", "typ": "JWT"})

    def test_payload_has_iss_sub_exp(self):
        payload_seg = self.client._jwt().split(".")[1]
        payload = json.loads(_b64url_decode(payload_seg))
        self.assertEqual(payload["iss"], "key-123")
        self.assertEqual(payload["sub"], "me@example.com")
        self.assertGreater(payload["exp"], int(time.time()))
        self.assertLessEqual(payload["exp"], int(time.time()) + 31)

    def test_signature_matches_independent_hmac(self):
        token = self.client._jwt()
        header_seg, payload_seg, sig_seg = token.split(".")
        expected = hmac.new(
            b"secret-456",
            f"{header_seg}.{payload_seg}".encode(),
            hashlib.sha256,
        ).digest()
        self.assertEqual(_b64url_decode(sig_seg), expected)

    def test_fresh_jwt_per_call(self):
        t1 = self.client._jwt()
        time.sleep(1.01)
        t2 = self.client._jwt()
        self.assertNotEqual(t1, t2)

    def test_partner_id_absent_when_not_provided(self):
        payload = json.loads(_b64url_decode(self.client._jwt().split(".")[1]))
        self.assertNotIn("partner_id", payload)

    def test_partner_id_included_when_provided(self):
        client = PdfGenApiClient(
            base_url="https://example.test/api/v4",
            api_key="k",
            api_secret="s",
            workspace_identifier="me@example.com",
            partner_id="odoo_v19",
        )
        payload = json.loads(_b64url_decode(client._jwt().split(".")[1]))
        self.assertEqual(payload["partner_id"], "odoo_v19")

    def test_sub_workspace_identifier_forwarded_verbatim(self):
        """pdfgeneratorapi.com routes sub-workspace traffic via the JWT `sub`
        claim. Whatever the admin types into Workspace Identifier must land
        in the JWT unchanged — no transformation, escaping, or splitting."""
        identifier = "master@domain.com:sub-workspace-slug"
        client = PdfGenApiClient(
            base_url="https://example.test/api/v4",
            api_key="k",
            api_secret="s",
            workspace_identifier=identifier,
        )
        payload = json.loads(_b64url_decode(client._jwt().split(".")[1]))
        self.assertEqual(payload["sub"], identifier)


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.client = PdfGenApiClient(
            base_url="https://example.test/api/v4",
            api_key="k",
            api_secret="s",
            workspace_identifier="w",
        )

    def _mock_response(self, *, ok=True, status=200, json_body=None, text=""):
        resp = MagicMock()
        resp.ok = ok
        resp.status_code = status
        resp.text = text or (json.dumps(json_body) if json_body is not None else "")
        resp.content = resp.text.encode()
        resp.json.return_value = json_body
        return resp

    def test_ping_calls_templates_endpoint(self):
        # `/workspaces/{id}` is master-user-only; ping uses `/templates` with a
        # minimal page so regular workspace users can validate their config too.
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(json_body={"response": []})
            self.client.ping()
        args, kwargs = mock_req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://example.test/api/v4/templates")
        self.assertEqual(kwargs["params"], {"per_page": 1, "page": 1})
        self.assertIn("Authorization", kwargs["headers"])
        self.assertTrue(kwargs["headers"]["Authorization"].startswith("Bearer "))

    def test_list_templates_forwards_pagination(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(json_body={"response": []})
            self.client.list_templates(per_page=50, page=2, name="invoice")
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs["params"], {"per_page": 50, "page": 2, "name": "invoice"})

    def test_generate_builds_body(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(json_body={"response": "abc"})
            self.client.generate(template_id=42, data={"x": 1}, name="demo.pdf")
        _, kwargs = mock_req.call_args
        self.assertEqual(
            kwargs["json"],
            {
                "template": {"id": 42, "data": {"x": 1}},
                "format": "pdf",
                "output": "base64",
                "name": "demo.pdf",
            },
        )

    def test_non_2xx_raises_pdfgen_error(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(
                ok=False,
                status=422,
                text='{"message":"bad"}',
            )
            with self.assertRaises(PdfGenApiError) as ctx:
                self.client.list_templates()
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("bad", ctx.exception.body)

    def test_timeout_is_wrapped_as_api_error(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.side_effect = _client_module.requests.Timeout()
            with self.assertRaises(PdfGenApiError) as ctx:
                self.client._request("GET", "/x", retries=0)
        self.assertEqual(ctx.exception.status, 0)
        self.assertIn("timed out", str(ctx.exception).lower())

    def test_base_url_trailing_slash_is_stripped(self):
        c = PdfGenApiClient(
            base_url="https://example.test/api/v4/",
            api_key="k",
            api_secret="s",
            workspace_identifier="w",
        )
        self.assertEqual(c.base_url, "https://example.test/api/v4")

    def test_list_templates_forwards_tags_and_access(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(json_body={"response": []})
            self.client.list_templates(tags="invoice", access="private")
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs["params"]["tags"], "invoice")
        self.assertEqual(kwargs["params"]["access"], "private")

    def test_network_error_is_wrapped(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.side_effect = _client_module.requests.RequestException("dns boom")
            with self.assertRaises(PdfGenApiError) as ctx:
                self.client._request("GET", "/x", retries=0)
        self.assertEqual(ctx.exception.status, 0)
        self.assertIn("dns boom", str(ctx.exception))

    def test_non_json_body_returns_raw_bytes(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            resp = MagicMock()
            resp.ok = True
            resp.status_code = 200
            resp.content = b"binary-bytes"
            resp.json.side_effect = ValueError("not json")
            mock_req.return_value = resp
            result = self.client._request("GET", "/raw")
        self.assertEqual(result, b"binary-bytes")

    def test_empty_response_body_returns_none(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            resp = MagicMock()
            resp.ok = True
            resp.status_code = 204
            resp.content = b""
            mock_req.return_value = resp
            result = self.client._request("DELETE", "/x")
        self.assertIsNone(result)

    def test_open_editor_posts_to_editor_endpoint(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(
                json_body={"response": "https://editor.test/signed?token=abc"}
            )
            url = self.client.open_editor(template_id=42)
        args, kwargs = mock_req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://example.test/api/v4/templates/42/editor")
        self.assertEqual(kwargs["json"], {})
        self.assertEqual(url, "https://editor.test/signed?token=abc")

    def test_open_editor_forwards_data_and_language(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(json_body={"response": "u"})
            self.client.open_editor(template_id=7, data={"x": 1}, language="es")
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs["json"], {"data": {"x": 1}, "language": "es"})

    def test_open_editor_rewrites_host_with_editor_web_url_override(self):
        # Simulates the Docker-internal setup: API returns a pdf-api-nginx URL,
        # browser needs localhost:8080.
        client = PdfGenApiClient(
            base_url="http://pdf-api-nginx/api/v4",
            api_key="k",
            api_secret="s",
            workspace_identifier="w",
            editor_web_url="http://localhost:8080",
        )
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(
                json_body={"response": "http://pdf-api-nginx/editor/42?token=SIGNED"}
            )
            url = client.open_editor(template_id=42)
        # Host rewritten; signed token preserved verbatim.
        self.assertEqual(url, "http://localhost:8080/editor/42?token=SIGNED")

    def test_generate_async_posts_async_endpoint(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(json_body={"response": {"id": "job-7"}})
            job_id = self.client.generate_async(
                template_id=42,
                data={"k": "v"},
                callback_url="https://odoo.example.com/pdfgen/webhook/deliver?j=1&t=tok",
                name="invoice.pdf",
            )
        args, kwargs = mock_req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://example.test/api/v4/documents/generate/async")
        self.assertEqual(
            kwargs["json"],
            {
                "template": {"id": 42, "data": {"k": "v"}},
                "format": "pdf",
                "output": "base64",
                "name": "invoice.pdf",
                "callback": {
                    "url": "https://odoo.example.com/pdfgen/webhook/deliver?j=1&t=tok",
                },
            },
        )
        self.assertEqual(job_id, "job-7")

    def test_extract_async_job_id_variants(self):
        extract = PdfGenApiClient._extract_async_job_id
        self.assertEqual(extract("job-7"), "job-7")
        self.assertEqual(extract({"response": "job-8"}), "job-8")
        self.assertEqual(extract({"response": {"id": "job-9"}}), "job-9")
        self.assertEqual(extract({"response": {"id": 42}}), "42")
        self.assertEqual(extract({"response": {"job_id": "abc"}}), "abc")
        self.assertEqual(extract({"response": {"uuid": "u"}}), "u")
        self.assertIsNone(extract({"response": {}}))
        self.assertIsNone(extract(99))

    def test_extract_editor_url_variants(self):
        extract = PdfGenApiClient._extract_editor_url
        self.assertEqual(extract("https://x"), "https://x")
        self.assertEqual(extract({"response": "https://y"}), "https://y")
        self.assertEqual(extract({"response": {"url": "https://z"}}), "https://z")
        self.assertEqual(extract({"response": {"editor_url": "https://w"}}), "https://w")
        self.assertIsNone(extract({"response": {"foo": "bar"}}))
        self.assertIsNone(extract(123))

    def test_rewrite_url_host_preserves_query(self):
        rewrite = PdfGenApiClient._rewrite_url_host
        self.assertEqual(
            rewrite("http://api.internal/editor/1?token=abc#frag", "http://outside:9000"),
            "http://outside:9000/editor/1?token=abc#frag",
        )

    def test_rewrite_url_host_ignores_override_path(self):
        # If the admin mistakenly points the override at a path-prefixed URL,
        # we must NOT prepend it to the original path — that double-prefixes
        # (e.g. `/editor/editor/open/{uuid}`) and pdfgen returns 400.
        rewrite = PdfGenApiClient._rewrite_url_host
        self.assertEqual(
            rewrite("http://api.internal/editor/open/abc", "http://outside/editor"),
            "http://outside/editor/open/abc",
        )

    def test_create_template_posts_to_templates_endpoint(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(
                json_body={"response": {"id": 99, "name": "My new template"}}
            )
            result = self.client.create_template("My new template")
        args, kwargs = mock_req.call_args
        self.assertEqual(args[0], "POST")
        self.assertEqual(args[1], "https://example.test/api/v4/templates")
        self.assertEqual(kwargs["json"], {"name": "My new template"})
        self.assertEqual(result["response"]["id"], 99)

    def test_create_template_forwards_description(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(json_body={"response": {"id": 1}})
            self.client.create_template("Tpl", description="a nice template")
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs["json"], {"name": "Tpl", "description": "a nice template"})

    def test_default_base_url_used_when_blank(self):
        c = PdfGenApiClient(
            base_url="",
            api_key="k",
            api_secret="s",
            workspace_identifier="w",
        )
        self.assertEqual(c.base_url, _client_module.DEFAULT_BASE_URL.rstrip("/"))


class LibraryTemplateTests(unittest.TestCase):
    """Template Library endpoints + `lib:` id normalization."""

    def setUp(self):
        self.client = PdfGenApiClient(
            base_url="https://example.test/api/v4",
            api_key="k",
            api_secret="s",
            workspace_identifier="w",
        )

    def _mock_response(self, *, json_body=None):
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.text = json.dumps(json_body) if json_body is not None else ""
        resp.content = resp.text.encode()
        resp.json.return_value = json_body
        return resp

    def test_normalize_template_id(self):
        normalize = _client_module.normalize_template_id
        self.assertEqual(normalize(42), 42)
        self.assertEqual(normalize("42"), 42)
        self.assertEqual(normalize("lib:abc123"), "abc123")
        # A numeric public id must stay a string — it lives in the public-id
        # namespace, not the account-template one.
        self.assertEqual(normalize("lib:42"), "42")
        self.assertEqual(normalize("weird"), "weird")

    def test_list_library_templates_uses_configured_base_url(self):
        # Each deployment (production, staging, regional, on-prem) serves its
        # own public library, so library calls follow the configured base URL
        # like every other call.
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(json_body={"response": []})
            self.client.list_library_templates()
        args, kwargs = mock_req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://example.test/api/v4/templates/library")
        self.assertIsNone(kwargs["params"])

    def test_list_library_templates_forwards_tags(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(json_body={"response": []})
            self.client.list_library_templates(tags="invoice")
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs["params"], {"tags": "invoice"})

    def test_get_library_template_hits_public_id_path(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(json_body={"response": {"name": "Tpl"}})
            self.client.get_library_template("abc123")
        args, _ = mock_req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://example.test/api/v4/templates/library/abc123")

    def test_generate_with_library_value_sends_public_id_string(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(json_body={"response": "b64"})
            self.client.generate(template_id="lib:abc123", data={"x": 1}, name="demo.pdf")
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs["json"]["template"], {"id": "abc123", "data": {"x": 1}})

    def test_generate_default_name_strips_library_prefix(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(json_body={"response": "b64"})
            self.client.generate(template_id="lib:abc123", data={})
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs["json"]["name"], "template-abc123")

    def test_generate_async_with_library_value_sends_public_id_string(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(json_body={"response": {"id": "j1"}})
            self.client.generate_async(
                template_id="lib:abc123",
                data={},
                callback_url="https://odoo.example.com/cb",
            )
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs["json"]["template"]["id"], "abc123")

    def test_open_editor_rejects_library_values(self):
        with self.assertRaises(PdfGenApiError):
            self.client.open_editor("lib:abc123")

    def test_create_template_from_definition_whitelists_keys(self):
        definition = {
            "id": "abc123",
            "name": "Invoice template",
            "tags": ["invoice"],
            "isDraft": False,
            "layout": {"format": "A4"},
            "pages": [{"components": []}],
            "dataSettings": {"sortBy": ""},
            "editor": {"heightMultiplier": 1},
            "some_future_readonly_key": "x",
        }
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(json_body={"response": {"id": 7}})
            self.client.create_template(definition=definition)
        _, kwargs = mock_req.call_args
        self.assertEqual(
            kwargs["json"],
            {
                "name": "Invoice template",
                "tags": ["invoice"],
                "isDraft": False,
                "layout": {"format": "A4"},
                "pages": [{"components": []}],
                "dataSettings": {"sortBy": ""},
                "editor": {"heightMultiplier": 1},
            },
        )

    def test_create_template_definition_name_override(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(json_body={"response": {"id": 7}})
            self.client.create_template(name="My copy", definition={"name": "Original"})
        _, kwargs = mock_req.call_args
        self.assertEqual(kwargs["json"], {"name": "My copy"})


class RetryTests(unittest.TestCase):
    """Retry loop in _request for 429 / 5xx gateway errors + network blips."""

    def setUp(self):
        self.client = PdfGenApiClient(
            base_url="https://example.test/api/v4",
            api_key="k",
            api_secret="s",
            workspace_identifier="w",
        )

    def _mock_response(self, *, ok=True, status=200, json_body=None, text="", headers=None):
        resp = MagicMock()
        resp.ok = ok
        resp.status_code = status
        resp.text = text or (json.dumps(json_body) if json_body is not None else "")
        resp.content = resp.text.encode()
        resp.json.return_value = json_body
        resp.headers = headers or {}
        return resp

    def test_429_twice_then_200_succeeds(self):
        responses = [
            self._mock_response(ok=False, status=429, text="slow down"),
            self._mock_response(ok=False, status=429, text="slow down"),
            self._mock_response(ok=True, json_body={"response": "ok"}),
        ]
        with (
            patch.object(_client_module.requests, "request", side_effect=responses) as mock_req,
            patch.object(_client_module.time, "sleep") as mock_sleep,
        ):
            result = self.client.ping()
        self.assertEqual(result, {"response": "ok"})
        self.assertEqual(mock_req.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    def test_retry_after_integer_header_is_honored(self):
        responses = [
            self._mock_response(ok=False, status=429, text="wait", headers={"Retry-After": "5"}),
            self._mock_response(ok=True, json_body={"response": "ok"}),
        ]
        with (
            patch.object(_client_module.requests, "request", side_effect=responses),
            patch.object(_client_module.time, "sleep") as mock_sleep,
        ):
            self.client.ping()
        # First (and only) sleep honors the header verbatim, up to cap.
        (waited,), _ = mock_sleep.call_args
        self.assertAlmostEqual(waited, 5.0, places=3)

    def test_retry_after_capped_at_max_single_wait(self):
        responses = [
            self._mock_response(ok=False, status=429, text="wait", headers={"Retry-After": "9999"}),
            self._mock_response(ok=True, json_body={"response": "ok"}),
        ]
        with (
            patch.object(_client_module.requests, "request", side_effect=responses),
            patch.object(_client_module.time, "sleep") as mock_sleep,
        ):
            self.client.ping()
        (waited,), _ = mock_sleep.call_args
        self.assertLessEqual(waited, _client_module.MAX_SINGLE_WAIT_SECONDS)

    def test_500_fails_immediately(self):
        with (
            patch.object(
                _client_module.requests,
                "request",
                return_value=self._mock_response(ok=False, status=500, text="boom"),
            ) as mock_req,
            patch.object(_client_module.time, "sleep") as mock_sleep,
            self.assertRaises(PdfGenApiError) as ctx,
        ):
            self.client.ping()
        self.assertEqual(ctx.exception.status, 500)
        self.assertEqual(mock_req.call_count, 1)
        mock_sleep.assert_not_called()

    def test_503_retries_then_gives_up(self):
        responses = [self._mock_response(ok=False, status=503, text="down") for _ in range(4)]
        with (
            patch.object(_client_module.requests, "request", side_effect=responses) as mock_req,
            patch.object(_client_module.time, "sleep"),
            self.assertRaises(PdfGenApiError) as ctx,
        ):
            self.client.ping()
        self.assertEqual(ctx.exception.status, 503)
        # DEFAULT_RETRIES=3 → 4 total attempts.
        self.assertEqual(mock_req.call_count, 4)

    def test_timeout_then_success(self):
        with (
            patch.object(
                _client_module.requests,
                "request",
                side_effect=[
                    _client_module.requests.Timeout(),
                    self._mock_response(ok=True, json_body={"response": "ok"}),
                ],
            ) as mock_req,
            patch.object(_client_module.time, "sleep"),
        ):
            result = self.client.ping()
        self.assertEqual(result, {"response": "ok"})
        self.assertEqual(mock_req.call_count, 2)

    def test_retries_zero_disables_loop(self):
        with (
            patch.object(
                _client_module.requests,
                "request",
                return_value=self._mock_response(ok=False, status=503, text="down"),
            ) as mock_req,
            patch.object(_client_module.time, "sleep") as mock_sleep,
            self.assertRaises(PdfGenApiError),
        ):
            self.client._request("GET", "/x", retries=0)
        self.assertEqual(mock_req.call_count, 1)
        mock_sleep.assert_not_called()

    def test_non_retryable_4xx_fails_fast(self):
        with (
            patch.object(
                _client_module.requests,
                "request",
                return_value=self._mock_response(ok=False, status=422, text="bad"),
            ) as mock_req,
            patch.object(_client_module.time, "sleep") as mock_sleep,
            self.assertRaises(PdfGenApiError),
        ):
            self.client.ping()
        self.assertEqual(mock_req.call_count, 1)
        mock_sleep.assert_not_called()

    def test_parse_retry_after_integer(self):
        self.assertAlmostEqual(_client_module._parse_retry_after("5"), 5.0)
        self.assertAlmostEqual(_client_module._parse_retry_after("0"), 0.0)

    def test_parse_retry_after_negative_clamped_to_zero(self):
        self.assertEqual(_client_module._parse_retry_after("-1"), 0.0)

    def test_parse_retry_after_http_date(self):
        # Always-past date should produce 0 (clamped).
        parsed = _client_module._parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT")
        self.assertEqual(parsed, 0.0)

    def test_parse_retry_after_garbage_returns_none(self):
        self.assertIsNone(_client_module._parse_retry_after("soon-ish"))


class RedactionTests(unittest.TestCase):
    """`_redact` protects log output from pdfgen error bodies echoing secrets."""

    def test_redacts_token_in_json_body(self):
        text = '{"token": "abc123xyz", "message": "bad"}'
        out = _client_module._redact(text)
        self.assertIn("<redacted>", out)
        self.assertNotIn("abc123xyz", out)
        self.assertIn("bad", out)  # unrelated fields pass through

    def test_redacts_bearer_header_style(self):
        text = "Authorization: Bearer eyJ.payload.sig"
        out = _client_module._redact(text)
        self.assertIn("<redacted>", out)
        self.assertNotIn("eyJ.payload.sig", out)

    def test_redacts_api_key_underscore_and_hyphen(self):
        for key in ("api_key", "api-key", "apikey", "API_KEY"):
            text = f'{key}="secretvalue"'
            out = _client_module._redact(text)
            self.assertNotIn("secretvalue", out, f"failed to redact {key}")

    def test_redacts_multiple_occurrences(self):
        text = 'token: a secret: b password="c"'
        out = _client_module._redact(text)
        for leaked in ("a", "b", "c"):
            self.assertNotIn(f" {leaked}", out, text)
        self.assertEqual(out.count("<redacted>"), 3)

    def test_plain_error_body_passes_through(self):
        text = '{"message": "Bad request", "status": 400}'
        self.assertEqual(_client_module._redact(text), text)

    def test_empty_and_none_are_safe(self):
        self.assertEqual(_client_module._redact(""), "")
        self.assertIsNone(_client_module._redact(None))

    def test_warning_log_is_redacted_end_to_end(self):
        """Prove the redaction is actually wired into the warning call site."""
        client = PdfGenApiClient(
            base_url="https://example.test/api/v4",
            api_key="k",
            api_secret="s",
            workspace_identifier="w",
        )
        body = '{"token": "LEAKED_TOKEN", "error": "boom"}'
        with (
            patch.object(_client_module.requests, "request") as mock_req,
            self.assertLogs(_client_module._logger, level="WARNING") as captured,
        ):
            mock_req.return_value = self._mock_response(ok=False, status=401, text=body)
            with self.assertRaises(PdfGenApiError):
                client.ping()
        joined = "\n".join(captured.output)
        self.assertIn("<redacted>", joined)
        self.assertNotIn("LEAKED_TOKEN", joined)

    def _mock_response(self, *, ok=True, status=200, json_body=None, text=""):
        resp = MagicMock()
        resp.ok = ok
        resp.status_code = status
        resp.text = text or (json.dumps(json_body) if json_body is not None else "")
        resp.content = resp.text.encode()
        resp.json.return_value = json_body
        return resp


class PdfGenApiErrorTests(unittest.TestCase):
    def test_default_message_includes_status_and_body(self):
        err = PdfGenApiError(500, "boom")
        self.assertEqual(err.status, 500)
        self.assertEqual(err.body, "boom")
        self.assertIn("500", str(err))
        self.assertIn("boom", str(err))

    def test_custom_message_is_honored(self):
        err = PdfGenApiError(0, "", "timed out")
        self.assertEqual(str(err), "timed out")
