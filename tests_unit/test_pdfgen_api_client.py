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
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

CLIENT_PATH = (
    Path(__file__).parent.parent / "pdfgeneratorapi_connector" / "models" / "pdfgen_api_client.py"
)
spec = importlib.util.spec_from_file_location("pdfgen_api_client", CLIENT_PATH)
_client_module = importlib.util.module_from_spec(spec)
sys.modules["pdfgen_api_client"] = _client_module
spec.loader.exec_module(_client_module)

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

    def test_ping_calls_workspaces_endpoint(self):
        with patch.object(_client_module.requests, "request") as mock_req:
            mock_req.return_value = self._mock_response(json_body={"response": {"id": 1}})
            self.client.ping()
        args, kwargs = mock_req.call_args
        self.assertEqual(args[0], "GET")
        self.assertEqual(args[1], "https://example.test/api/v4/workspaces/w")
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
                self.client.ping()
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
                self.client.ping()
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

    def test_default_base_url_used_when_blank(self):
        c = PdfGenApiClient(
            base_url="",
            api_key="k",
            api_secret="s",
            workspace_identifier="w",
        )
        self.assertEqual(c.base_url, _client_module.DEFAULT_BASE_URL.rstrip("/"))


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
