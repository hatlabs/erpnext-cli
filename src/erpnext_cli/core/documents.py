"""ERPNext document CRUD operations."""

import json
import re
import urllib.parse

from erpnext_cli.core.client import ERPNextClient, ERPNextAPIError

# Validation patterns matching the MCP server
DOCTYPE_NAME_RE = re.compile(r"^[\w -]+$")
FIELD_NAME_RE = re.compile(r"^\w+$")


def _validate_doctype(name: str) -> None:
    if not DOCTYPE_NAME_RE.match(name):
        raise ERPNextAPIError(f"Invalid DocType name: {name!r}")


def _validate_field(name: str, label: str) -> None:
    if not FIELD_NAME_RE.match(name):
        raise ERPNextAPIError(f"Invalid field name in {label}: {name!r}")


def _encode_doctype(doctype: str) -> str:
    return urllib.parse.quote(doctype, safe="")


def list_documents(
    client: ERPNextClient,
    doctype: str,
    filters: dict | None = None,
    fields: list[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Query a list of documents."""
    _validate_doctype(doctype)

    params = {}
    if fields:
        params["fields"] = json.dumps(fields)
    if filters:
        params["filters"] = json.dumps(filters)
    if limit is not None:
        params["limit_page_length"] = str(limit)

    resp = client._request(f"/api/resource/{_encode_doctype(doctype)}", params=params)
    return resp.get("data", [])


def get_document(client: ERPNextClient, doctype: str, name: str) -> dict:
    """Fetch a single document by DocType and name."""
    _validate_doctype(doctype)

    resp = client._request(
        f"/api/resource/{_encode_doctype(doctype)}/{urllib.parse.quote(name, safe='')}"
    )
    return resp.get("data", {})


def create_document(client: ERPNextClient, doctype: str, data: dict) -> dict:
    """Create a new document."""
    _validate_doctype(doctype)

    resp = client._request(
        f"/api/resource/{_encode_doctype(doctype)}",
        method="POST",
        data={"data": data},
    )
    doc = resp.get("data", {})
    return {
        "status": "success",
        "doctype": doctype,
        "name": doc.get("name"),
        "docstatus": doc.get("docstatus", 0),
    }


def update_document(
    client: ERPNextClient, doctype: str, name: str, data: dict
) -> dict:
    """Update an existing document."""
    _validate_doctype(doctype)

    resp = client._request(
        f"/api/resource/{_encode_doctype(doctype)}/{urllib.parse.quote(name, safe='')}",
        method="PUT",
        data={"data": data},
    )
    doc = resp.get("data", {})
    return {
        "status": "success",
        "doctype": doctype,
        "name": doc.get("name"),
        "docstatus": doc.get("docstatus", 0),
    }


def submit_document(client: ERPNextClient, doctype: str, name: str) -> dict:
    """Submit a document (set docstatus=1). Irreversible."""
    return update_document(client, doctype, name, {"docstatus": 1})


def cancel_document(client: ERPNextClient, doctype: str, name: str) -> dict:
    """Cancel a submitted document via frappe.client.cancel."""
    _validate_doctype(doctype)

    from erpnext_cli.core.methods import call_method

    call_method(client, "frappe.client.cancel", args={"doctype": doctype, "name": name})
    return {
        "status": "success",
        "doctype": doctype,
        "name": name,
        "docstatus": 2,
    }


def _parent_filters_to_tuples(
    doctype: str, filters: dict
) -> list[list]:
    """Convert {field: value} filter dict to Frappe 4-tuple filter list."""
    tuples = []
    for field, value in filters.items():
        _validate_field(field, "parent_filters")
        if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
            tuples.append([doctype, field, value[0], value[1]])
        else:
            tuples.append([doctype, field, "=", value])
    return tuples


def get_child_documents(
    client: ERPNextClient,
    parent_doctype: str,
    child_doctype: str,
    parent_fields: list[str] | None = None,
    child_fields: list[str] | None = None,
    child_filters: list[list] | None = None,
    parent_filters: dict | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Query child table rows via parent-child join.

    Direct child table queries return 403; this uses frappe.client.get_list
    with backtick-quoted field prefixes.
    """
    _validate_doctype(parent_doctype)
    _validate_doctype(child_doctype)

    p_fields = parent_fields or ["name"]
    for f in p_fields:
        _validate_field(f, "parent_fields")

    c_fields = child_fields or []
    for f in c_fields:
        _validate_field(f, "child_fields")

    # Frappe convention: table name is `tab{DocType}`
    child_table = f"tab{child_doctype}"
    fields = list(p_fields) + [f"`{child_table}`.{f}" for f in c_fields]

    # Build filter tuples
    all_filters = []
    if child_filters:
        for cf in child_filters:
            if len(cf) != 3:
                raise ERPNextAPIError(
                    f"Child filter must be [field, operator, value], got: {cf!r}"
                )
            _validate_field(cf[0], "child_filters")
            all_filters.append([child_doctype, cf[0], cf[1], cf[2]])

    if parent_filters:
        all_filters.extend(_parent_filters_to_tuples(parent_doctype, parent_filters))

    from erpnext_cli.core.methods import call_method

    args: dict = {
        "doctype": parent_doctype,
        "fields": fields,
        "limit_page_length": limit or 100,
    }
    if all_filters:
        args["filters"] = all_filters

    result = call_method(client, "frappe.client.get_list", args=args)
    return result or []
