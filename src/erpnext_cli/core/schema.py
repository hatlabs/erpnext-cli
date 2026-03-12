"""ERPNext DocType schema introspection."""

import json

from erpnext_cli.core.client import ERPNextClient, ERPNextAPIError


def list_doctypes(client: ERPNextClient, limit: int = 500) -> list[str]:
    """Get a list of all available DocTypes."""
    try:
        resp = client._request(
            "/api/resource/DocType",
            params={
                "fields": json.dumps(["name"]),
                "limit_page_length": str(limit),
            },
        )
        return [item["name"] for item in resp.get("data", [])]
    except ERPNextAPIError:
        # Fallback to search_link method
        resp = client._request(
            "/api/method/frappe.desk.search.search_link",
            params={"doctype": "DocType", "txt": "", "limit": str(limit)},
        )
        msg = resp.get("message") or resp.get("results") or []
        return [item.get("value") or item.get("name", "") for item in msg]


def get_doctype_fields(client: ERPNextClient, doctype: str) -> list[dict]:
    """Get field definitions for a DocType.

    Returns fieldname, fieldtype, label, options, and reqd for each field.
    """
    import urllib.parse

    resp = client._request(
        f"/api/resource/DocType/{urllib.parse.quote(doctype, safe='')}"
    )
    raw_fields = resp.get("data", {}).get("fields", [])

    return [
        {
            "fieldname": f["fieldname"],
            "fieldtype": f["fieldtype"],
            "label": f.get("label", ""),
            "options": f.get("options") or None,
            "reqd": f.get("reqd", 0),
        }
        for f in raw_fields
    ]
