"""HTTP client for pdfgeneratorapi.com v4.

Hand-rolled against stdlib + requests (already in Odoo's base image) so the
module has zero external pip dependencies. Auth is JWT Bearer (HS256) per
https://docs.pdfgeneratorapi.com/v4 — iss=API key, sub=workspace identifier,
exp=short TTL. Fresh JWT per request.
"""

import base64
import hashlib
import hmac
import json
import logging
import time

import requests

_logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://us1.pdfgeneratorapi.com/api/v4"
DEFAULT_TIMEOUT = 60
JWT_TTL_SECONDS = 30


class PdfGenApiError(Exception):
    """Raised when the API returns a non-2xx response."""

    def __init__(self, status, body, message=None):
        self.status = status
        self.body = body
        super().__init__(message or f"PDF Generator API error {status}: {body}")


class PdfGenApiClient:
    """Minimal client covering the endpoints the Odoo addon needs for v1."""

    def __init__(
        self, base_url, api_key, api_secret, workspace_identifier, timeout=DEFAULT_TIMEOUT
    ):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.workspace = workspace_identifier
        self.timeout = timeout

    @staticmethod
    def _b64url(payload):
        return base64.urlsafe_b64encode(payload).rstrip(b"=")

    def _jwt(self, ttl=JWT_TTL_SECONDS):
        header = self._b64url(
            json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode()
        )
        payload = self._b64url(
            json.dumps(
                {
                    "iss": self.api_key,
                    "sub": self.workspace,
                    "exp": int(time.time()) + ttl,
                },
                separators=(",", ":"),
            ).encode()
        )
        signing_input = header + b"." + payload
        signature = self._b64url(
            hmac.new(self.api_secret.encode(), signing_input, hashlib.sha256).digest()
        )
        return (signing_input + b"." + signature).decode()

    def _request(self, method, path, params=None, json_body=None):
        url = f"{self.base_url}{path}"
        try:
            response = requests.request(
                method,
                url,
                params=params,
                json=json_body,
                timeout=self.timeout,
                headers={
                    "Authorization": f"Bearer {self._jwt()}",
                    "Accept": "application/json",
                    "User-Agent": "pdfgeneratorapi-odoo-connector/1.0",
                },
            )
        except requests.Timeout as e:
            raise PdfGenApiError(0, "", "Request timed out") from e
        except requests.RequestException as e:
            raise PdfGenApiError(0, "", f"Network error: {e}") from e
        if not response.ok:
            _logger.warning(
                "PDF Generator API %s %s → %s %s",
                method,
                path,
                response.status_code,
                response.text[:500],
            )
            raise PdfGenApiError(response.status_code, response.text)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.content

    def ping(self):
        """Validate both auth and workspace existence."""
        return self._request("GET", f"/workspaces/{self.workspace}")

    def list_templates(self, per_page=100, page=1, name=None, tags=None, access=None):
        params = {"per_page": per_page, "page": page}
        if name:
            params["name"] = name
        if tags:
            params["tags"] = tags
        if access:
            params["access"] = access
        return self._request("GET", "/templates", params=params)

    def get_template_data(self, template_id):
        """Return the sample data dict the template expects.

        The response envelope is `{"response": <dict>, "meta": {}}`. The dict shape
        defines every placeholder path (nested dicts + arrays of dicts) the template
        will interpolate.
        """
        return self._request("GET", f"/templates/{int(template_id)}/data")

    def generate(self, template_id, data, name=None, output="base64", fmt="pdf"):
        body = {
            "template": {"id": int(template_id), "data": data},
            "format": fmt,
            "output": output,
            "name": name or f"template-{template_id}",
        }
        return self._request("POST", "/documents/generate", json_body=body)
