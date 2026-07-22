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

from ..enums import Format, Output

_logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://us1.pdfgeneratorapi.com/api/v4"
DEFAULT_TIMEOUT = 60
JWT_TTL_SECONDS = 30

# Parsed return of `_request`: a decoded JSON body (usually a dict envelope,
# sometimes a list or a bare string for scalar endpoints), the raw bytes when
# the response isn't JSON, or None for an empty 2xx body.
ApiResponse = dict | list | str | bytes | None

# Selection-value prefix marking a Template Library ("default") template.
# Library templates are identified by an opaque public id string, while
# account templates use numeric ids — the prefix keeps the two namespaces
# unambiguous in Selection fields, attachment descriptions and job rows.
LIBRARY_TEMPLATE_PREFIX = "lib:"

# The public library serves every integration's templates (invoices, KYC
# forms, FDA filings…). Only the `odoo`-tagged ones are built against the
# datasets this connector ships, so the dropdown filters on this tag —
# without it users are offered templates whose placeholders nothing here
# populates.
LIBRARY_TAG = "odoo"


def normalize_template_id(value: str | int) -> int | str:
    """Turn a selection/storage value into what the API expects.

    `lib:`-prefixed values resolve to the bare public id *string* (the
    generation endpoints accept either a numeric id or a public id).
    Numeric values stay ints so request bodies are unchanged for account
    templates. Anything else passes through as a string.
    """
    text = str(value)
    if text.startswith(LIBRARY_TEMPLATE_PREFIX):
        return text[len(LIBRARY_TEMPLATE_PREFIX) :]
    try:
        return int(text)
    except (TypeError, ValueError):
        return text


# Retry policy for _request. 429 and the three "gateway-ish" 5xx codes are
# retried with exponential backoff. Everything else either succeeds on first
# try or fails fast — no point retrying 401/403/404/422/500 (non-gateway).
DEFAULT_RETRIES = 3
RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})
MAX_SINGLE_WAIT_SECONDS = 10
MAX_TOTAL_WAIT_SECONDS = 30

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


def _redact(text: str) -> str:
    """Mask values after secret-sounding keys in free-form text.

    Used before logging API response bodies so a stray token in an error
    payload doesn't land in server logs. Non-matching text passes through
    untouched.
    """
    if not text:
        return text
    return _REDACT_RE.sub(r"\1<redacted>", text)


def _parse_retry_after(header_value: str) -> float | None:
    """Parse a `Retry-After` header. Returns float seconds or None on junk.

    RFC 7231 allows either a non-negative integer (seconds) or an HTTP-date.
    We try integer first, then email.utils.parsedate_to_datetime for the
    date form. Clamp negative values to 0.
    """
    header_value = header_value.strip()
    try:
        seconds = float(header_value)
        return max(0.0, seconds)
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        target = parsedate_to_datetime(header_value)
        if target is None:
            return None
        delta = target.timestamp() - time.time()
        return max(0.0, delta)
    except (TypeError, ValueError):
        return None


class PdfGenApiError(Exception):
    """Raised when the API returns a non-2xx response."""

    def __init__(self, status: int | None, body: str | None, message: str | None = None) -> None:
        self.status = status
        self.body = body
        super().__init__(message or f"PDF Generator API error {status}: {body}")


class PdfGenApiClient:
    """Minimal client covering the endpoints the Odoo addon needs for v1."""

    def __init__(
        self,
        base_url: str | None,
        api_key: str,
        api_secret: str,
        workspace_identifier: str,
        timeout: int = DEFAULT_TIMEOUT,
        editor_web_url: str | None = None,
        partner_id: str | None = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.workspace = workspace_identifier
        self.timeout = timeout
        self.partner_id = partner_id
        # Browser-facing host for the editor. When None, we derive from base_url
        # by stripping the /api/vN suffix — correct for pdfgeneratorapi.com's
        # hosted service. Needed as an explicit override when the API is reached
        # via a hostname the browser can't resolve (Docker service name, private
        # VPC endpoint, …).
        self.editor_web_url = editor_web_url.rstrip("/") if editor_web_url else None

    @staticmethod
    def _b64url(payload: bytes) -> bytes:
        return base64.urlsafe_b64encode(payload).rstrip(b"=")

    def _jwt(self, ttl: int = JWT_TTL_SECONDS) -> str:
        header = self._b64url(
            json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode()
        )
        claims = {
            "iss": self.api_key,
            "sub": self.workspace,
            "exp": int(time.time()) + ttl,
        }
        if self.partner_id:
            claims["partner_id"] = self.partner_id
        payload = self._b64url(json.dumps(claims, separators=(",", ":")).encode())
        signing_input = header + b"." + payload
        signature = self._b64url(
            hmac.new(self.api_secret.encode(), signing_input, hashlib.sha256).digest()
        )
        return (signing_input + b"." + signature).decode()

    def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        json_body: dict | None = None,
        retries: int = DEFAULT_RETRIES,
    ) -> ApiResponse:
        """HTTP call with retry + backoff on transient failures.

        Retries on connection errors, timeouts, and responses with status in
        RETRYABLE_STATUSES (429 / 502 / 503 / 504). Honors `Retry-After` when
        present; otherwise uses exponential backoff (2 ** attempt seconds)
        with ±20% jitter, capped at MAX_SINGLE_WAIT_SECONDS per sleep and
        MAX_TOTAL_WAIT_SECONDS overall. Non-retryable 4xx and 5xx still fail
        on first response.
        """
        elapsed_wait = 0.0
        last_error = None
        last_response = None
        # attempts run 0..retries inclusive; `retries=3` → up to 4 tries total,
        # matching the industry convention that "3 retries" means "3 retries
        # after the initial attempt". We cap at `retries` to keep the API
        # small: callers pass retries=0 to opt out entirely.
        attempts = max(1, retries + 1)
        for attempt in range(attempts):
            try:
                response = requests.request(
                    method,
                    f"{self.base_url}{path}",
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
                last_error = PdfGenApiError(0, "", "Request timed out")
                last_error.__cause__ = e
                last_response = None
            except requests.RequestException as e:
                last_error = PdfGenApiError(0, "", f"Network error: {e}")
                last_error.__cause__ = e
                last_response = None
            else:
                if response.ok:
                    if not response.content:
                        return None
                    try:
                        return response.json()
                    except ValueError:
                        return response.content
                last_error = None
                last_response = response
                if response.status_code not in RETRYABLE_STATUSES:
                    break
            # Retryable error (exception or retryable status). Sleep unless
            # this was our last attempt.
            if attempt + 1 >= attempts:
                break
            wait = self._retry_delay(attempt, last_response)
            if elapsed_wait + wait > MAX_TOTAL_WAIT_SECONDS:
                wait = max(0, MAX_TOTAL_WAIT_SECONDS - elapsed_wait)
                if wait == 0:
                    break
            _logger.info(
                "PDF Generator API %s %s → retry %d/%d after %.1fs",
                method,
                path,
                attempt + 1,
                attempts - 1,
                wait,
            )
            time.sleep(wait)
            elapsed_wait += wait

        # Exhausted retries (or hit a non-retryable status). Surface the same
        # error shape as before the retry was added.
        if last_response is not None:
            _logger.warning(
                "PDF Generator API %s %s → %s %s",
                method,
                path,
                last_response.status_code,
                _redact(last_response.text)[:500],
            )
            raise PdfGenApiError(last_response.status_code, last_response.text)
        # last_response is None only on the exception paths, which always set
        # last_error — the `or` fallback is just to satisfy the type checker
        # (and avoid ever raising None) for the theoretically-unreachable case.
        raise last_error or PdfGenApiError(0, "", "Request failed")

    @staticmethod
    def _retry_delay(attempt: int, response: requests.Response | None) -> float:
        """Compute how long to wait before the next retry.

        Prefers `Retry-After` from the response (integer seconds or HTTP-date).
        Falls back to exponential `2 ** attempt` with ±20% jitter. Capped at
        MAX_SINGLE_WAIT_SECONDS so a misbehaving server with a huge Retry-After
        can't wedge the caller.
        """
        import random

        if response is not None:
            header = response.headers.get("Retry-After", "") if response.headers else ""
            if header:
                parsed = _parse_retry_after(header)
                if parsed is not None:
                    return min(parsed, MAX_SINGLE_WAIT_SECONDS)
        base = 2**attempt
        jitter = base * 0.2 * (random.random() * 2 - 1)
        return max(0, min(base + jitter, MAX_SINGLE_WAIT_SECONDS))

    def ping(self) -> ApiResponse:
        """Validate auth + workspace by listing one template.

        `/workspaces/{id}` is restricted to master-user credentials, so it
        rejects regular workspace users with a 403 even when the JWT and
        workspace identifier are correct. `/templates` is reachable by any
        authenticated workspace (master or sub), so it makes a better health
        check — a 200 proves the API key/secret and `sub` claim resolve to a
        real workspace, without requiring elevated permissions.
        """
        return self._request("GET", "/templates", params={"per_page": 1, "page": 1})

    def list_templates(
        self,
        per_page: int = 100,
        page: int = 1,
        name: str | None = None,
        tags: str | None = None,
        access: str | None = None,
    ) -> ApiResponse:
        params: dict[str, int | str] = {"per_page": per_page, "page": page}
        if name:
            params["name"] = name
        if tags:
            params["tags"] = tags
        if access:
            params["access"] = access
        return self._request("GET", "/templates", params=params)

    # The Template Library is public content, but it is *per deployment*: each
    # environment (production, staging, a regional or on-prem install) serves
    # its own set of public templates. Library calls therefore go to the
    # configured base URL like every other call — pinning them to production
    # made a staging-configured Odoo list production's "Default Templates" and
    # then copy them into a staging workspace.
    def list_library_templates(self, tags: str | None = None) -> ApiResponse:
        """List the public Template Library (`GET /templates/library`).

        The endpoint requires no auth and has no pagination — the only
        filter is `tags`. Each entry carries a string `id` (public id),
        `name`, `tags`, `preview_image` and `sample_data` URLs.
        """
        params = {}
        if tags:
            params["tags"] = tags
        return self._request("GET", "/templates/library", params=params or None)

    def get_library_template(self, public_id: str) -> ApiResponse:
        """Fetch a library template's full definition
        (`GET /templates/library/{publicId}`). The `response` envelope holds a
        TemplateDefinition (name, layout, pages, dataSettings, editor) that can
        be POSTed to `/templates` to copy the template into the account.
        """
        return self._request("GET", f"/templates/library/{public_id}")

    def get_template_data(self, template_id: int | str) -> ApiResponse:
        """Return the sample data dict the template expects.

        The response envelope is `{"response": <dict>, "meta": {}}`. The dict shape
        defines every placeholder path (nested dicts + arrays of dicts) the template
        will interpolate.

        Takes an account template id. `lib:` values must be resolved to an
        account copy first (`pdfgen_resolve_template_id`) — this endpoint has
        no library counterpart.
        """
        return self._request("GET", f"/templates/{normalize_template_id(template_id)}/data")

    def generate(
        self,
        template_id: int | str,
        data: dict,
        name: str | None = None,
        output: Output = Output.BASE64,
        format: Format = Format.PDF,
    ) -> ApiResponse:
        normalized = normalize_template_id(template_id)
        body = {
            "template": {"id": normalized, "data": data},
            "format": format,
            "output": output,
            "name": name or f"template-{normalized}",
        }
        return self._request("POST", "/documents/generate", json_body=body)

    def generate_async(
        self,
        template_id: int | str,
        data: dict,
        callback_url: str,
        name: str | None = None,
        output: Output = Output.BASE64,
        format: Format = Format.PDF,
    ) -> str | None:
        """Dispatch an async generation job and return pdfgen's job id.

        Body shape mirrors `/documents/generate` plus a `callback.url` field
        pdfgen POSTs back to once the PDF is ready. The exact key name is
        the only piece tied to pdfgen's docs — the rest of the body / the
        envelope extractor are unchanging.
        """
        normalized = normalize_template_id(template_id)
        body = {
            "template": {"id": normalized, "data": data},
            "format": format,
            "output": output,
            "name": name or f"template-{normalized}",
            "callback": {"url": callback_url},
        }
        response = self._request("POST", "/documents/generate/async", json_body=body)
        return self._extract_async_job_id(response)

    @staticmethod
    def _extract_async_job_id(response: ApiResponse) -> str | None:
        """Pull the async-job id out of pdfgen's response envelope.

        Tolerant of both `{"response": "<id>"}` and the more typical
        `{"response": {"id": "<id>", ...}}` shapes.
        """
        if isinstance(response, str):
            return response
        if isinstance(response, dict):
            value = response.get("response")
            if isinstance(value, str):
                return value
            if isinstance(value, dict):
                for key in ("id", "job_id", "uuid"):
                    if value.get(key):
                        return str(value[key])
        return None

    def open_editor(
        self, template_id: int | str, data: dict | None = None, language: str | None = None
    ) -> str | None:
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
        if str(template_id).startswith(LIBRARY_TEMPLATE_PREFIX):
            # Library templates have no editor endpoint — callers must copy
            # them into the account first (see the template editor wizard).
            raise PdfGenApiError(
                None,
                None,
                message=f"Library template {template_id} cannot be opened in the editor directly",
            )
        body: dict[str, object] = {}
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
    def _extract_editor_url(response: ApiResponse) -> str | None:
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
    def _rewrite_url_host(url: str, new_host_base: str) -> str:
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

    # Keys POST /templates accepts (the spec's TemplateDefinitionNew schema).
    # Whitelisted (rather than just dropping `id`) so read-only metadata the
    # library may grow later never leaks into the body.
    TEMPLATE_DEFINITION_KEYS = (
        "name",
        "tags",
        "isDraft",
        "layout",
        "pages",
        "dataSettings",
        "editor",
        "fontSubsetting",
        "barcodeAsImage",
    )

    def create_template(
        self,
        name: str | None = None,
        description: str | None = None,
        definition: dict | None = None,
    ) -> ApiResponse:
        """Create a new template. Returns the template's metadata, including
        `id`, which the caller typically pairs with `get_editor_url` to immediately
        open the editor on the fresh template.

        With only `name`/`description` a blank template is minted. When
        `definition` is given (a TemplateDefinition dict, e.g. from
        `get_library_template`) its design is copied — `name` still wins if
        passed explicitly.
        """
        if definition is not None:
            body = {k: definition[k] for k in self.TEMPLATE_DEFINITION_KEYS if k in definition}
            if name:
                body["name"] = name
        else:
            body = {"name": name}
        if not body.get("name"):
            body["name"] = "New template"
        if description:
            body["description"] = description
        return self._request("POST", "/templates", json_body=body)
