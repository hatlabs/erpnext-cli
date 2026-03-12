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
