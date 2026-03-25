"""ERPNext file upload, download, and attachment operations."""

import json
import mimetypes
import os

from erpnext_cli.core.client import ERPNextClient, ERPNextAPIError
from erpnext_cli.core.documents import _validate_doctype, update_document


def upload_file(
    client: ERPNextClient,
    file_path: str,
    *,
    doctype: str | None = None,
    docname: str | None = None,
    is_private: bool = True,
    field: str | None = None,
) -> dict:
    """Upload a local file to ERPNext, optionally attaching to a document.

    If field is set, also updates that field on the target document
    with the uploaded file_url (e.g., field="image" on an Item).
    Requires doctype and docname to be set.
    """
    if not os.path.isfile(file_path):
        raise ERPNextAPIError(f"File not found: {file_path}")

    if field and not (doctype and docname):
        raise ERPNextAPIError("--field requires --doctype and --docname")

    if doctype:
        _validate_doctype(doctype)

    filename = os.path.basename(file_path)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    with open(file_path, "rb") as f:
        file_data = f.read()

    fields: dict[str, str] = {"is_private": "1" if is_private else "0"}
    if doctype:
        fields["doctype"] = doctype
    if docname:
        fields["docname"] = docname

    resp = client._request_multipart(
        "/api/method/upload_file",
        fields=fields,
        files={"file": (filename, file_data, content_type)},
    )
    result = resp.get("message", resp)

    if field and doctype and docname:
        file_url = result.get("file_url")
        if file_url:
            update_document(client, doctype, docname, {field: file_url})

    return result


def list_attachments(
    client: ERPNextClient,
    doctype: str,
    docname: str,
) -> list[dict]:
    """List files attached to a specific document."""
    _validate_doctype(doctype)

    filters = json.dumps([
        ["attached_to_doctype", "=", doctype],
        ["attached_to_name", "=", docname],
    ])
    fields = json.dumps([
        "name", "file_name", "file_url", "is_private", "file_size",
    ])

    resp = client._request(
        "/api/resource/File",
        params={"filters": filters, "fields": fields},
    )
    return resp.get("data", [])


_VALID_FILE_URL_PREFIXES = ("/files/", "/private/files/")


def download_file(
    client: ERPNextClient,
    file_url: str,
) -> tuple[bytes, str]:
    """Download a file by its file_url. Returns (bytes, content_type)."""
    if not any(file_url.startswith(p) for p in _VALID_FILE_URL_PREFIXES):
        raise ERPNextAPIError(
            f"Invalid file URL: {file_url!r} "
            f"(must start with /files/ or /private/files/)"
        )
    return client._request_binary(file_url)
