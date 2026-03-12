"""ERPNext report execution."""

import json

from erpnext_cli.core.client import ERPNextClient


def run_report(
    client: ERPNextClient,
    report_name: str,
    filters: dict | None = None,
) -> dict:
    """Execute an ERPNext saved report."""
    params: dict = {"report_name": report_name}
    if filters:
        params["filters"] = json.dumps(filters)

    resp = client._request(
        "/api/method/frappe.desk.query_report.run",
        params=params,
    )
    return resp.get("message", {})
