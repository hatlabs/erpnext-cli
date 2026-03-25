"""ERPNext REST/RPC API client."""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass


class ERPNextAPIError(Exception):
    """Raised when the ERPNext API returns an error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ERPNextRateLimitError(ERPNextAPIError):
    """Raised when ERPNext rate limit is hit."""

    def __init__(self, retry_after: float = 1.0):
        super().__init__(f"Rate limited. Retry after {retry_after}s", status_code=429)
        self.retry_after = retry_after


def extract_error_detail(error: Exception) -> str:
    """Extract human-readable error from ERPNext's nested JSON error responses.

    ERPNext returns _server_messages as a JSON array of JSON strings,
    each containing a .message field. Without parsing, callers only see
    opaque HTTP status codes like 417.
    """
    if isinstance(error, urllib.error.HTTPError):
        try:
            body = error.read().decode("utf-8", errors="replace")
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return f"HTTP {error.code}: {body[:500] if 'body' in dir() else str(error)}"

        if "_server_messages" in data:
            try:
                msgs = json.loads(data["_server_messages"])
                details = []
                for m in msgs[:5]:
                    try:
                        parsed = json.loads(m)
                        msg = parsed.get("message") if isinstance(parsed, dict) else None
                        if msg:
                            details.append(msg)
                        # If JSON parses but has no .message key, skip it
                    except (json.JSONDecodeError, TypeError):
                        # Not valid JSON — use the raw string
                        details.append(str(m))
                details = [d for d in details if d]
                if details:
                    joined = "; ".join(details)
                    return joined[:1000] + "..." if len(joined) > 1000 else joined
            except (json.JSONDecodeError, TypeError):
                pass

        if data.get("message"):
            return str(data["message"])
        if data.get("exc_type"):
            return str(data["exc_type"])

        return f"HTTP {error.code}"

    return str(error)


@dataclass
class ERPNextClient:
    """HTTP client for ERPNext REST/RPC API."""

    url: str
    api_key: str
    api_secret: str

    @property
    def _auth_header(self) -> str:
        return f"token {self.api_key}:{self.api_secret}"

    def _request(
        self,
        path: str,
        method: str = "GET",
        data: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """Make an HTTP request to ERPNext and return parsed JSON."""
        url = f"{self.url}{path}"
        if params:
            qs = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
            url = f"{url}?{qs}"

        body = json.dumps(data).encode("utf-8") if data else None
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": self._auth_header,
        }

        for attempt in range(2):
            req = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    retry_after = float(e.headers.get("Retry-After", "2.0"))
                    if attempt == 0:
                        time.sleep(retry_after)
                        continue
                    raise ERPNextRateLimitError(retry_after) from e
                detail = extract_error_detail(e)
                raise ERPNextAPIError(detail, status_code=e.code) from e
            except urllib.error.URLError as e:
                raise ERPNextAPIError(f"Connection error: {e.reason}") from e

        raise ERPNextAPIError("Request failed after retry")

    @staticmethod
    def _build_multipart_body(
        fields: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> tuple[bytes, str]:
        """Build a multipart/form-data body and return (body_bytes, content_type)."""
        boundary = uuid.uuid4().hex
        parts: list[bytes] = []

        for name, value in (fields or {}).items():
            parts.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n'
                f"\r\n"
                f"{value}\r\n".encode("utf-8")
            )

        for field_name, (filename, data, content_type) in (files or {}).items():
            header = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n"
                f"\r\n"
            ).encode("utf-8")
            parts.append(header + data + b"\r\n")

        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        body = b"".join(parts)
        ct = f"multipart/form-data; boundary={boundary}"
        return body, ct

    def _request_multipart(
        self,
        path: str,
        fields: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
    ) -> dict:
        """POST multipart/form-data and return parsed JSON."""
        url = f"{self.url}{path}"
        body, content_type = self._build_multipart_body(fields, files)
        headers = {
            "Content-Type": content_type,
            "Accept": "application/json",
            "Authorization": self._auth_header,
        }

        for attempt in range(2):
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    retry_after = float(e.headers.get("Retry-After", "2.0"))
                    if attempt == 0:
                        time.sleep(retry_after)
                        continue
                    raise ERPNextRateLimitError(retry_after) from e
                detail = extract_error_detail(e)
                raise ERPNextAPIError(detail, status_code=e.code) from e
            except urllib.error.URLError as e:
                raise ERPNextAPIError(f"Connection error: {e.reason}") from e

        raise ERPNextAPIError("Request failed after retry")

    def _request_binary(
        self,
        path: str,
    ) -> tuple[bytes, str]:
        """GET a path and return (raw_bytes, content_type)."""
        url = f"{self.url}{path}"
        headers = {"Authorization": self._auth_header}

        for attempt in range(2):
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req) as resp:
                    content_type = resp.headers.get("Content-Type", "application/octet-stream")
                    return resp.read(), content_type
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    retry_after = float(e.headers.get("Retry-After", "2.0"))
                    if attempt == 0:
                        time.sleep(retry_after)
                        continue
                    raise ERPNextRateLimitError(retry_after) from e
                detail = extract_error_detail(e)
                raise ERPNextAPIError(detail, status_code=e.code) from e
            except urllib.error.URLError as e:
                raise ERPNextAPIError(f"Connection error: {e.reason}") from e

        raise ERPNextAPIError("Request failed after retry")


def make_client(
    url: str | None = None,
    api_key: str | None = None,
    api_secret: str | None = None,
) -> ERPNextClient:
    """Create an ERPNextClient from explicit args or environment variables."""
    url = url or os.environ.get("ERPNEXT_URL")
    api_key = api_key or os.environ.get("ERPNEXT_API_KEY")
    api_secret = api_secret or os.environ.get("ERPNEXT_API_SECRET")

    if not url:
        raise ERPNextAPIError(
            "ERPNEXT_URL not set. Provide --url or set the env var."
        )
    if not api_key:
        raise ERPNextAPIError(
            "ERPNEXT_API_KEY not set. Provide --api-key or set the env var."
        )
    if not api_secret:
        raise ERPNextAPIError(
            "ERPNEXT_API_SECRET not set. Provide --api-secret or set the env var."
        )

    # Normalize trailing slash
    url = url.rstrip("/")

    return ERPNextClient(url=url, api_key=api_key, api_secret=api_secret)
