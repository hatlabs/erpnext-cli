"""Unit tests for erpnext-cli core modules."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from erpnext_cli.core.client import (
    ERPNextAPIError,
    ERPNextClient,
    extract_error_detail,
    make_client,
)
from erpnext_cli.core.strip import (
    strip_document,
    strip_child_row,
)
from erpnext_cli.core.documents import (
    _validate_doctype,
    _validate_field,
)
from erpnext_cli.core.methods import METHOD_PATH_RE
from erpnext_cli.core import files


# ---------------------------------------------------------------------------
# client.py
# ---------------------------------------------------------------------------


class TestMakeClient:
    def test_from_explicit_args(self):
        client = make_client(
            url="https://erp.example.com",
            api_key="key123",
            api_secret="secret456",
        )
        assert client.url == "https://erp.example.com"
        assert client.api_key == "key123"
        assert client.api_secret == "secret456"

    def test_from_env_vars(self, monkeypatch):
        monkeypatch.setenv("ERPNEXT_URL", "https://env.example.com")
        monkeypatch.setenv("ERPNEXT_API_KEY", "envkey")
        monkeypatch.setenv("ERPNEXT_API_SECRET", "envsecret")
        client = make_client()
        assert client.url == "https://env.example.com"
        assert client.api_key == "envkey"

    def test_missing_url_raises(self, monkeypatch):
        monkeypatch.delenv("ERPNEXT_URL", raising=False)
        monkeypatch.delenv("ERPNEXT_API_KEY", raising=False)
        monkeypatch.delenv("ERPNEXT_API_SECRET", raising=False)
        with pytest.raises(ERPNextAPIError, match="ERPNEXT_URL"):
            make_client(api_key="k", api_secret="s")

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("ERPNEXT_URL", raising=False)
        monkeypatch.delenv("ERPNEXT_API_KEY", raising=False)
        monkeypatch.delenv("ERPNEXT_API_SECRET", raising=False)
        with pytest.raises(ERPNextAPIError, match="ERPNEXT_API_KEY"):
            make_client(url="https://erp.example.com", api_secret="s")

    def test_missing_secret_raises(self, monkeypatch):
        monkeypatch.delenv("ERPNEXT_URL", raising=False)
        monkeypatch.delenv("ERPNEXT_API_KEY", raising=False)
        monkeypatch.delenv("ERPNEXT_API_SECRET", raising=False)
        with pytest.raises(ERPNextAPIError, match="ERPNEXT_API_SECRET"):
            make_client(url="https://erp.example.com", api_key="k")

    def test_explicit_args_override_env(self, monkeypatch):
        monkeypatch.setenv("ERPNEXT_URL", "https://env.example.com")
        monkeypatch.setenv("ERPNEXT_API_KEY", "envkey")
        monkeypatch.setenv("ERPNEXT_API_SECRET", "envsecret")
        client = make_client(
            url="https://explicit.example.com",
            api_key="explicitkey",
            api_secret="explicitsecret",
        )
        assert client.url == "https://explicit.example.com"
        assert client.api_key == "explicitkey"

    def test_trailing_slash_stripped(self):
        client = make_client(
            url="https://erp.example.com/",
            api_key="k",
            api_secret="s",
        )
        assert client.url == "https://erp.example.com"


class TestERPNextClient:
    def test_auth_header(self):
        client = ERPNextClient(url="https://erp.example.com", api_key="k", api_secret="s")
        assert client._auth_header == "token k:s"


class TestExtractErrorDetail:
    def test_plain_error(self):
        err = Exception("something went wrong")
        assert extract_error_detail(err) == "something went wrong"

    def test_server_messages(self):
        """Simulate ERPNext's nested JSON error format."""
        import urllib.error
        import io

        body = json.dumps({
            "_server_messages": json.dumps([
                json.dumps({"message": "Field X is mandatory"}),
                json.dumps({"message": "Missing value for Y"}),
            ])
        }).encode()
        resp = io.BytesIO(body)
        err = urllib.error.HTTPError(
            url="http://x", code=417, msg="", hdrs={}, fp=resp
        )
        detail = extract_error_detail(err)
        assert "Field X is mandatory" in detail
        assert "Missing value for Y" in detail

    def test_message_field(self):
        import urllib.error
        import io

        body = json.dumps({"message": "Not permitted"}).encode()
        resp = io.BytesIO(body)
        err = urllib.error.HTTPError(
            url="http://x", code=403, msg="", hdrs={}, fp=resp
        )
        assert extract_error_detail(err) == "Not permitted"

    def test_exc_type_fallback(self):
        import urllib.error
        import io

        body = json.dumps({"exc_type": "ValidationError"}).encode()
        resp = io.BytesIO(body)
        err = urllib.error.HTTPError(
            url="http://x", code=417, msg="", hdrs={}, fp=resp
        )
        assert extract_error_detail(err) == "ValidationError"

    def test_plain_string_server_messages(self):
        """ERPNext sometimes returns non-JSON entries in _server_messages."""
        import urllib.error
        import io

        body = json.dumps({
            "_server_messages": json.dumps(["plain error text"])
        }).encode()
        resp = io.BytesIO(body)
        err = urllib.error.HTTPError(
            url="http://x", code=417, msg="", hdrs={}, fp=resp
        )
        assert extract_error_detail(err) == "plain error text"

    def test_empty_server_messages_falls_through(self):
        import urllib.error
        import io

        body = json.dumps({
            "_server_messages": "[]",
            "message": "Something went wrong",
        }).encode()
        resp = io.BytesIO(body)
        err = urllib.error.HTTPError(
            url="http://x", code=400, msg="", hdrs={}, fp=resp
        )
        assert extract_error_detail(err) == "Something went wrong"

    def test_malformed_server_messages_falls_through(self):
        import urllib.error
        import io

        body = json.dumps({
            "_server_messages": "not valid json",
            "message": "Request failed",
        }).encode()
        resp = io.BytesIO(body)
        err = urllib.error.HTTPError(
            url="http://x", code=417, msg="", hdrs={}, fp=resp
        )
        assert extract_error_detail(err) == "Request failed"

    def test_server_message_without_message_key(self):
        """JSON-parseable entry but no .message key — should be filtered out."""
        import urllib.error
        import io

        body = json.dumps({
            "_server_messages": json.dumps([json.dumps({"title": "Error"})])
        }).encode()
        resp = io.BytesIO(body)
        err = urllib.error.HTTPError(
            url="http://x", code=417, msg="", hdrs={}, fp=resp
        )
        detail = extract_error_detail(err)
        # Falls through since message key is missing, hits HTTP code fallback
        assert "417" in detail or detail == "HTTP 417"

    def test_truncates_large_server_messages(self):
        import urllib.error
        import io

        msgs = [
            json.dumps({"message": f"Error {i}: {'x' * 100}"})
            for i in range(10)
        ]
        body = json.dumps({
            "_server_messages": json.dumps(msgs)
        }).encode()
        resp = io.BytesIO(body)
        err = urllib.error.HTTPError(
            url="http://x", code=417, msg="", hdrs={}, fp=resp
        )
        detail = extract_error_detail(err)
        # Only first 5 messages
        assert "Error 5:" not in detail
        # Capped at 1000 chars + "..."
        assert len(detail) <= 1003


# ---------------------------------------------------------------------------
# strip.py
# ---------------------------------------------------------------------------


class TestStripDocument:
    def test_strips_system_fields(self):
        doc = {
            "name": "ITEM-001",
            "item_name": "Widget",
            "owner": "admin@example.com",
            "modified_by": "admin@example.com",
            "creation": "2024-01-01",
            "modified": "2024-01-02",
            "idx": 0,
            "doctype": "Item",
            "_liked_by": "[]",
            "docstatus": 0,
            "status": "Active",
        }
        result = strip_document(doc)
        assert result == {
            "name": "ITEM-001",
            "item_name": "Widget",
            "docstatus": 0,
            "status": "Active",
        }

    def test_preserves_always_keep(self):
        doc = {"name": "X", "docstatus": 0, "status": ""}
        result = strip_document(doc)
        assert "name" in result
        assert "docstatus" in result
        assert "status" in result

    def test_strips_null_empty(self):
        doc = {"name": "X", "description": None, "notes": "", "docstatus": 0}
        result = strip_document(doc)
        assert "description" not in result
        assert "notes" not in result

    def test_preserves_zero_values(self):
        """Zero is a meaningful value (qty=0, rate=0) — must not be stripped."""
        doc = {"name": "X", "qty": 0, "rate": 0.0, "operating_cost": 0, "docstatus": 0}
        result = strip_document(doc)
        assert result["qty"] == 0
        assert result["rate"] == 0.0
        assert result["operating_cost"] == 0

    def test_preserves_false_values(self):
        doc = {"name": "X", "is_active": False, "docstatus": 0}
        result = strip_document(doc)
        assert result["is_active"] is False

    def test_preserves_empty_arrays(self):
        doc = {"name": "X", "tags": [], "docstatus": 0}
        result = strip_document(doc)
        assert result["tags"] == []

    def test_preserves_empty_child_tables(self):
        doc = {"name": "X", "docstatus": 0, "items": []}
        result = strip_document(doc)
        assert result["items"] == []

    def test_preserves_status_even_when_empty_string(self):
        doc = {"name": "X", "docstatus": 0, "status": ""}
        result = strip_document(doc)
        assert result["status"] == ""

    def test_auto_includes_child_table_key_from_child_fields(self):
        doc = {
            "name": "X", "docstatus": 0, "item": "ITEM-001",
            "items": [{"name": "r1", "item_code": "A", "qty": 2}],
        }
        result = strip_document(doc, fields=["item"], child_fields={"items": ["item_code"]})
        assert "items" in result
        assert result["item"] == "ITEM-001"

    def test_field_selection(self):
        doc = {"name": "X", "item_name": "W", "description": "D", "docstatus": 0}
        result = strip_document(doc, fields=["item_name"])
        assert "item_name" in result
        assert "name" in result  # always kept
        assert "description" not in result

    def test_child_table_stripping(self):
        doc = {
            "name": "BOM-001",
            "docstatus": 1,
            "items": [
                {
                    "name": "row1",
                    "item_code": "PART-A",
                    "qty": 5,
                    "owner": "admin",
                    "_comment_count": 0,
                    "idx": 1,
                }
            ],
        }
        result = strip_document(doc)
        assert len(result["items"]) == 1
        row = result["items"][0]
        assert row["name"] == "row1"
        assert row["item_code"] == "PART-A"
        assert "owner" not in row
        assert "_comment_count" not in row

    def test_child_field_selection(self):
        doc = {
            "name": "BOM-001",
            "docstatus": 1,
            "items": [
                {"name": "r1", "item_code": "A", "qty": 5, "rate": 10.0}
            ],
        }
        result = strip_document(doc, child_fields={"items": ["item_code", "qty"]})
        row = result["items"][0]
        assert "item_code" in row
        assert "qty" in row
        assert "rate" not in row
        assert "name" in row  # always kept


class TestStripChildRow:
    def test_basic_strip(self):
        row = {"name": "r1", "item_code": "A", "owner": "admin", "_x": "y", "empty_field": ""}
        result = strip_child_row(row)
        assert result == {"name": "r1", "item_code": "A"}

    def test_field_selection(self):
        row = {"name": "r1", "item_code": "A", "qty": 5, "rate": 10}
        result = strip_child_row(row, fields=["item_code"])
        assert "item_code" in result
        assert "name" in result
        assert "qty" not in result


# ---------------------------------------------------------------------------
# documents.py validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_doctype_names(self):
        for name in ["Item", "Sales Order", "BOM Item", "Work Order"]:
            _validate_doctype(name)  # should not raise

    def test_invalid_doctype_rejects(self):
        for name in ["Item;DROP", "../../etc", "Item\nInject"]:
            with pytest.raises(ERPNextAPIError, match="Invalid DocType"):
                _validate_doctype(name)

    def test_valid_field_names(self):
        for name in ["item_code", "qty", "rate", "name"]:
            _validate_field(name, "test")  # should not raise

    def test_invalid_field_rejects(self):
        for name in ["item code", "rate;1", "field.name"]:
            with pytest.raises(ERPNextAPIError, match="Invalid field"):
                _validate_field(name, "test")


# ---------------------------------------------------------------------------
# methods.py validation
# ---------------------------------------------------------------------------


class TestMethodPathValidation:
    def test_valid_paths(self):
        for path in [
            "frappe.client.get_list",
            "frappe.client.cancel",
            "erpnext.manufacturing.doctype.work_order.work_order.make_stock_entry",
        ]:
            assert METHOD_PATH_RE.match(path)

    def test_invalid_paths(self):
        for path in ["frappe/client", "method;inject", "path with spaces"]:
            assert not METHOD_PATH_RE.match(path)


# ---------------------------------------------------------------------------
# client.py — multipart and binary methods
# ---------------------------------------------------------------------------


class TestBuildMultipartBody:
    def test_fields_only(self):
        body, ct = ERPNextClient._build_multipart_body(
            fields={"doctype": "Item", "is_private": "1"},
        )
        assert b"multipart/form-data" not in body  # that's in ct
        assert ct.startswith("multipart/form-data; boundary=")
        assert b'name="doctype"' in body
        assert b"Item" in body
        assert b'name="is_private"' in body
        assert b"1" in body

    def test_files_only(self):
        body, ct = ERPNextClient._build_multipart_body(
            files={"file": ("test.txt", b"hello world", "text/plain")},
        )
        assert b'name="file"' in body
        assert b'filename="test.txt"' in body
        assert b"Content-Type: text/plain" in body
        assert b"hello world" in body

    def test_fields_and_files(self):
        body, ct = ERPNextClient._build_multipart_body(
            fields={"doctype": "Item"},
            files={"file": ("img.png", b"\x89PNG", "image/png")},
        )
        boundary = ct.split("boundary=")[1]
        assert body.count(f"--{boundary}".encode()) == 3  # 2 parts + closing

    def test_binary_file_data_preserved(self):
        binary_data = bytes(range(256))
        body, _ = ERPNextClient._build_multipart_body(
            files={"file": ("data.bin", binary_data, "application/octet-stream")},
        )
        assert binary_data in body

    def test_empty_body(self):
        body, ct = ERPNextClient._build_multipart_body()
        boundary = ct.split("boundary=")[1]
        # Just the closing boundary
        assert body == f"--{boundary}--\r\n".encode()


# ---------------------------------------------------------------------------
# files.py
# ---------------------------------------------------------------------------


class TestUploadFile:
    def test_upload_calls_multipart(self, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        client = ERPNextClient(url="https://erp.example.com", api_key="k", api_secret="s")
        client._request_multipart = MagicMock(return_value={
            "message": {"name": "abc123", "file_url": "/files/test.txt"}
        })

        result = files.upload_file(client, str(test_file))

        client._request_multipart.assert_called_once()
        call_args = client._request_multipart.call_args
        assert call_args[0][0] == "/api/method/upload_file"
        assert call_args[1]["fields"]["is_private"] == "1"
        assert "file" in call_args[1]["files"]
        assert result["file_url"] == "/files/test.txt"

    def test_upload_with_doctype_docname(self, tmp_path):
        test_file = tmp_path / "doc.pdf"
        test_file.write_bytes(b"pdf content")

        client = ERPNextClient(url="https://erp.example.com", api_key="k", api_secret="s")
        client._request_multipart = MagicMock(return_value={"message": {"name": "x"}})

        files.upload_file(client, str(test_file), doctype="Item", docname="ITEM-001")

        fields = client._request_multipart.call_args[1]["fields"]
        assert fields["doctype"] == "Item"
        assert fields["docname"] == "ITEM-001"

    def test_upload_public(self, tmp_path):
        test_file = tmp_path / "pub.txt"
        test_file.write_text("public")

        client = ERPNextClient(url="https://erp.example.com", api_key="k", api_secret="s")
        client._request_multipart = MagicMock(return_value={"message": {}})

        files.upload_file(client, str(test_file), is_private=False)

        fields = client._request_multipart.call_args[1]["fields"]
        assert fields["is_private"] == "0"

    def test_upload_missing_file(self):
        client = ERPNextClient(url="https://erp.example.com", api_key="k", api_secret="s")
        with pytest.raises(ERPNextAPIError, match="File not found"):
            files.upload_file(client, "/nonexistent/file.txt")

    def test_upload_invalid_doctype(self, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_text("x")

        client = ERPNextClient(url="https://erp.example.com", api_key="k", api_secret="s")
        with pytest.raises(ERPNextAPIError, match="Invalid DocType"):
            files.upload_file(client, str(test_file), doctype="Item;DROP")

    def test_upload_with_field_updates_document(self, tmp_path):
        test_file = tmp_path / "photo.jpg"
        test_file.write_bytes(b"jpeg data")

        client = ERPNextClient(url="https://erp.example.com", api_key="k", api_secret="s")
        client._request_multipart = MagicMock(return_value={
            "message": {"name": "f1", "file_url": "/files/photo.jpg"}
        })
        client._request = MagicMock(return_value={
            "data": {"name": "ITEM-001", "docstatus": 0}
        })

        result = files.upload_file(
            client, str(test_file),
            doctype="Item", docname="ITEM-001", field="image",
        )

        # Verify upload happened
        client._request_multipart.assert_called_once()
        # Verify document was updated with file_url
        client._request.assert_called_once()
        update_call = client._request.call_args
        assert update_call[1]["data"] == {"data": {"image": "/files/photo.jpg"}}

    def test_upload_field_without_doctype_raises(self, tmp_path):
        test_file = tmp_path / "photo.jpg"
        test_file.write_bytes(b"jpeg data")

        client = ERPNextClient(url="https://erp.example.com", api_key="k", api_secret="s")
        with pytest.raises(ERPNextAPIError, match="--field requires --doctype and --docname"):
            files.upload_file(client, str(test_file), field="image")


class TestListAttachments:
    def test_list_calls_request_with_filters(self):
        client = ERPNextClient(url="https://erp.example.com", api_key="k", api_secret="s")
        client._request = MagicMock(return_value={
            "data": [{"name": "f1", "file_name": "doc.pdf", "file_url": "/files/doc.pdf"}]
        })

        result = files.list_attachments(client, "Item", "ITEM-001")

        client._request.assert_called_once()
        call_args = client._request.call_args
        assert call_args[0][0] == "/api/resource/File"
        params = call_args[1]["params"]
        parsed_filters = json.loads(params["filters"])
        assert ["attached_to_doctype", "=", "Item"] in parsed_filters
        assert ["attached_to_name", "=", "ITEM-001"] in parsed_filters
        assert len(result) == 1

    def test_list_invalid_doctype(self):
        client = ERPNextClient(url="https://erp.example.com", api_key="k", api_secret="s")
        with pytest.raises(ERPNextAPIError, match="Invalid DocType"):
            files.list_attachments(client, "Item;DROP", "x")


class TestDownloadFile:
    def test_download_calls_binary(self):
        client = ERPNextClient(url="https://erp.example.com", api_key="k", api_secret="s")
        client._request_binary = MagicMock(return_value=(b"file content", "text/plain"))

        data, ct = files.download_file(client, "/files/test.txt")

        client._request_binary.assert_called_once_with("/files/test.txt")
        assert data == b"file content"
        assert ct == "text/plain"

    def test_download_private_file_url(self):
        client = ERPNextClient(url="https://erp.example.com", api_key="k", api_secret="s")
        client._request_binary = MagicMock(return_value=(b"data", "image/png"))

        data, ct = files.download_file(client, "/private/files/img.png")

        client._request_binary.assert_called_once_with("/private/files/img.png")

    def test_download_rejects_invalid_url(self):
        client = ERPNextClient(url="https://erp.example.com", api_key="k", api_secret="s")

        with pytest.raises(ERPNextAPIError, match="Invalid file URL"):
            files.download_file(client, "/api/method/something")

    def test_download_rejects_absolute_url(self):
        client = ERPNextClient(url="https://erp.example.com", api_key="k", api_secret="s")

        with pytest.raises(ERPNextAPIError, match="Invalid file URL"):
            files.download_file(client, "https://evil.com/files/steal.txt")
