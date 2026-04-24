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
import re
import time

import requests

_logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://us1.pdfgeneratorapi.com/api/v4"
DEFAULT_TIMEOUT = 60
JWT_TTL_SECONDS = 30

# Matches `<secret-key>: <value>` / `<secret-key>="<value>"` / etc. across
# typical log formats. We redact the value so error bodies logged at WARN
# can't leak tokens even if pdfgen echoes them back in a 4xx/5xx payload.
# Key names are case-insensitive; value runs until whitespace, quote, comma,
# or closing brace.
_REDACT_RE = re.compile(
    r"((?:token|secret|authorization|bearer|jwt|apikey|api[_-]?key|password)"
    r'["\']?\s*[:=]\s*(?:Bearer\s+)?["\']?)'
    r'([^\s"\',}]+)',
    re.IGNORECASE,
)


def _redact(text):
    """Mask values after secret-sounding keys in free-form text.

    Used before logging API response bodies so a stray token in an error
    payload doesn't land in server logs. Non-matching text passes through
    untouched.
    """
    if not text:
        return text
    return _REDACT_RE.sub(r"\1<redacted>", text)


class PdfGenApiError(Exception):
    """Raised when the API returns a non-2xx response."""

    def __init__(self, status, body, message=None):
        self.status = status
        self.body = body
        super().__init__(message or f"PDF Generator API error {status}: {body}")


class PdfGenApiClient:
    """Minimal client covering the endpoints the Odoo addon needs for v1."""

    def __init__(
        self,
        base_url,
        api_key,
        api_secret,
        workspace_identifier,
        timeout=DEFAULT_TIMEOUT,
        editor_web_url=None,
    ):
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.workspace = workspace_identifier
        self.timeout = timeout
        # Browser-facing host for the editor. When None, we derive from base_url
        # by stripping the /api/vN suffix — correct for pdfgeneratorapi.com's
        # hosted service. Needed as an explicit override when the API is reached
        # via a hostname the browser can't resolve (Docker service name, private
        # VPC endpoint, …).
        self.editor_web_url = editor_web_url.rstrip("/") if editor_web_url else None

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
            # Redact before truncating so a token straddling the 500-char
            # boundary is still caught by the regex.
            _logger.warning(
                "PDF Generator API %s %s → %s %s",
                method,
                path,
                response.status_code,
                _redact(response.text)[:500],
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

    def open_editor(self, template_id, data=None, language=None):
        """Call `POST /templates/{id}/editor` (openEditor) and return the signed
        URL pointing at the embedded template editor.

        Per https://docs.pdfgeneratorapi.com/v4#tag/Templates/operation/openEditor
        the response envelope is `{"response": "<signed url string>"}`. The URL
        is time-limited and intended for redirect or iframe src — don't cache
        beyond the current action.

        When the server-side API is reached via a hostname the browser can't
        resolve (Docker service name, private VPC endpoint, …) the admin can
        set `editor_web_url` — we rewrite the returned URL's scheme+host to
        match, preserving the path and (crucially) the signed token in the
        query string.
        """
        body = {}
        if data is not None:
            body["data"] = data
        if language:
            body["language"] = language
        response = self._request("POST", f"/templates/{int(template_id)}/editor", json_body=body)
        url = self._extract_editor_url(response)
        if url and self.editor_web_url:
            url = self._rewrite_url_host(url, self.editor_web_url)
        return url

    @staticmethod
    def _extract_editor_url(response):
        """Pull the signed URL string out of an openEditor response."""
        if isinstance(response, str):
            return response
        if isinstance(response, dict):
            value = response.get("response")
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                for key in ("url", "editor_url", "link"):
                    if isinstance(value.get(key), str):
                        return value[key]
        return None

    @staticmethod
    def _rewrite_url_host(url, new_host_base):
        """Replace the scheme+host of `url` with `new_host_base`. Keeps path,
        query and fragment verbatim so the signed session identifier (typically
        embedded in the path as a UUID) survives intact.

        Any path in `new_host_base` is ignored — admins should set the override
        to a bare origin like `http://pdfgeneratorapi.test`, not a path-prefixed
        URL. Including a prefix was a footgun: the API's returned URL already
        starts with `/editor/...`, and concatenating produced `/editor/editor/...`.
        """
        from urllib.parse import urlparse, urlunparse

        original = urlparse(url)
        replacement = urlparse(new_host_base.rstrip("/"))
        return urlunparse(
            (
                replacement.scheme or original.scheme,
                replacement.netloc or original.netloc,
                original.path,
                original.params,
                original.query,
                original.fragment,
            )
        )

    def create_template(self, name, description=None):
        """Create a new blank template. Returns the template's metadata, including
        `id`, which the caller typically pairs with `get_editor_url` to immediately
        open the editor on the fresh template."""
        body = {"name": name}
        if description:
            body["description"] = description
        return self._request("POST", "/templates", json_body=body)
