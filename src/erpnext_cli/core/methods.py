"""ERPNext whitelisted method API calls."""

import re

from erpnext_cli.core.client import ERPNextClient, ERPNextAPIError

METHOD_PATH_RE = re.compile(r"^[\w.]+$")


def call_method(
    client: ERPNextClient,
    method: str,
    args: dict | None = None,
    http_method: str = "POST",
) -> dict | list | None:
    """Call a whitelisted Frappe/ERPNext server method.

    Args are passed as JSON body (POST) or query params (GET).
    """
    if not METHOD_PATH_RE.match(method):
        raise ERPNextAPIError(f"Invalid method path: {method!r}")

    if http_method.upper() == "GET":
        resp = client._request(f"/api/method/{method}", params=args)
    else:
        resp = client._request(f"/api/method/{method}", method="POST", data=args)

    return resp.get("message")
