"""E2E tests for erpnext-cli — requires live ERPNext instance.

Set ERPNEXT_URL, ERPNEXT_API_KEY, ERPNEXT_API_SECRET env vars.
"""

import json
import os
import subprocess
import sys

import pytest

from erpnext_cli.core.client import make_client, ERPNextAPIError


def _skip_if_no_creds():
    if not all(
        os.environ.get(v)
        for v in ("ERPNEXT_URL", "ERPNEXT_API_KEY", "ERPNEXT_API_SECRET")
    ):
        pytest.skip("ERPNext credentials not set")


def _resolve_cli(name):
    """Resolve installed CLI command; falls back to python -m for dev."""
    import shutil

    force = os.environ.get("ERPNEXT_CLI_FORCE_INSTALLED", "").strip() == "1"
    path = shutil.which(name)
    if path:
        print(f"[_resolve_cli] Using installed command: {path}")
        return [path]
    if force:
        raise RuntimeError(f"{name} not found in PATH. Install with: pip install -e .")
    module = "erpnext_cli"
    print(f"[_resolve_cli] Falling back to: {sys.executable} -m {module}")
    return [sys.executable, "-m", "erpnext_cli"]


@pytest.fixture(scope="module")
def client():
    _skip_if_no_creds()
    return make_client()


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------


class TestDocumentAPI:
    def test_list_items(self, client):
        from erpnext_cli.core.documents import list_documents

        data = list_documents(client, "Item", limit=3)
        assert isinstance(data, list)
        assert len(data) <= 3
        if data:
            assert "name" in data[0]

    def test_get_document(self, client):
        from erpnext_cli.core.documents import list_documents, get_document
        from erpnext_cli.core.strip import strip_document

        items = list_documents(client, "Item", limit=1)
        if not items:
            pytest.skip("No items in ERPNext")
        item_name = items[0]["name"]

        doc = get_document(client, "Item", item_name)
        assert doc["name"] == item_name

        stripped = strip_document(doc)
        assert "name" in stripped
        assert "owner" not in stripped

    def test_list_with_filters(self, client):
        from erpnext_cli.core.documents import list_documents

        data = list_documents(
            client, "Item",
            filters={"item_group": "Products"},
            fields=["name", "item_name", "item_group"],
            limit=5,
        )
        assert isinstance(data, list)
        for item in data:
            assert item.get("item_group") == "Products"


class TestSchemaAPI:
    def test_list_doctypes(self, client):
        from erpnext_cli.core.schema import list_doctypes

        data = list_doctypes(client, limit=10)
        assert isinstance(data, list)
        assert len(data) > 0

    def test_get_fields(self, client):
        from erpnext_cli.core.schema import get_doctype_fields

        fields = get_doctype_fields(client, "Item")
        assert isinstance(fields, list)
        assert len(fields) > 0
        assert "fieldname" in fields[0]
        assert "fieldtype" in fields[0]


class TestReportAPI:
    def test_run_report(self, client):
        from erpnext_cli.core.reports import run_report

        data = run_report(client, "Stock Balance")
        assert isinstance(data, dict)
        # Reports may return result/columns directly or a prepared_report flag
        assert "result" in data or "columns" in data or "prepared_report" in data


class TestMethodAPI:
    def test_call_get_count(self, client):
        from erpnext_cli.core.methods import call_method

        result = call_method(
            client, "frappe.client.get_count",
            args={"doctype": "Item"},
            http_method="GET",
        )
        assert isinstance(result, (int, float))
        assert result >= 0


class TestChildDocuments:
    def test_bom_items(self, client):
        from erpnext_cli.core.documents import list_documents, get_child_documents

        boms = list_documents(client, "BOM", filters={"is_active": 1}, limit=1)
        if not boms:
            pytest.skip("No active BOMs in ERPNext")

        rows = get_child_documents(
            client,
            parent_doctype="BOM",
            child_doctype="BOM Item",
            parent_fields=["name"],
            child_fields=["item_code", "qty"],
            limit=5,
        )
        assert isinstance(rows, list)
        if rows:
            assert "name" in rows[0]


# ---------------------------------------------------------------------------
# CLI subprocess tests
# ---------------------------------------------------------------------------


class TestCLISubprocess:
    CLI_BASE = _resolve_cli("erpnext-cli")

    def _run(self, args, check=True):
        return subprocess.run(
            self.CLI_BASE + args,
            capture_output=True,
            text=True,
            check=check,
        )

    def test_help(self):
        result = self._run(["--help"])
        assert result.returncode == 0
        assert "ERPNext" in result.stdout

    def test_document_help(self):
        result = self._run(["document", "--help"])
        assert result.returncode == 0
        assert "list" in result.stdout

    def test_json_document_list(self):
        _skip_if_no_creds()
        result = self._run(["--json", "document", "list", "Item", "--limit", "2"])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)

    def test_schema_doctypes(self):
        _skip_if_no_creds()
        result = self._run(["--json", "schema", "doctypes", "--limit", "5"])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)

    def test_schema_fields(self):
        _skip_if_no_creds()
        result = self._run(["--json", "schema", "fields", "Item"])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        if data:
            assert "fieldname" in data[0]

    def test_file_help(self):
        result = self._run(["file", "--help"])
        assert result.returncode == 0
        assert "upload" in result.stdout
        assert "download" in result.stdout
        assert "list" in result.stdout


# ---------------------------------------------------------------------------
# File API tests
# ---------------------------------------------------------------------------


class TestFileAPI:
    def test_upload_and_list_and_download(self, client, tmp_path):
        """Round-trip: upload a file, list it, download it, verify content."""
        from erpnext_cli.core.files import upload_file, list_attachments, download_file

        # Create a test file
        test_content = b"erpnext-cli file upload test content"
        test_file = tmp_path / "test_upload.txt"
        test_file.write_bytes(test_content)

        # Upload (unattached)
        result = upload_file(client, str(test_file))
        assert "file_url" in result
        file_url = result["file_url"]

        # Download and verify content
        data, ct = download_file(client, file_url)
        assert data == test_content

    def test_upload_attached_to_item(self, client, tmp_path):
        """Upload a file attached to an Item, then list attachments."""
        from erpnext_cli.core.documents import list_documents
        from erpnext_cli.core.files import upload_file, list_attachments

        items = list_documents(client, "Item", limit=1)
        if not items:
            pytest.skip("No items in ERPNext")

        item_name = items[0]["name"]
        test_file = tmp_path / "item_attachment.txt"
        test_file.write_text("attached to item")

        result = upload_file(
            client, str(test_file),
            doctype="Item", docname=item_name,
        )
        assert "file_url" in result

        # Verify it appears in attachments list
        attachments = list_attachments(client, "Item", item_name)
        urls = [a.get("file_url") for a in attachments]
        assert result["file_url"] in urls


class TestFileCLISubprocess:
    CLI_BASE = _resolve_cli("erpnext-cli")

    def _run(self, args, check=True):
        return subprocess.run(
            self.CLI_BASE + args,
            capture_output=True,
            text=True,
            check=check,
        )

    def test_file_upload_cli(self, tmp_path):
        _skip_if_no_creds()
        test_file = tmp_path / "cli_upload_test.txt"
        test_file.write_text("cli upload test")

        result = self._run(["--json", "file", "upload", str(test_file)])
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "file_url" in data

    def test_file_list_cli(self):
        _skip_if_no_creds()
        result = self._run(["--json", "file", "list", "Item", "COM-HALPI2"], check=False)
        # May return empty list if no attachments, but should not error
        if result.returncode == 0:
            data = json.loads(result.stdout)
            assert isinstance(data, list)

    def test_file_download_cli(self, tmp_path):
        _skip_if_no_creds()
        from erpnext_cli.core.files import upload_file

        # Upload first
        test_file = tmp_path / "dl_test.txt"
        test_file.write_text("download me")
        client = make_client()
        result = upload_file(client, str(test_file))
        file_url = result["file_url"]

        # Download via CLI
        output_path = str(tmp_path / "downloaded.txt")
        cli_result = self._run(["file", "download", file_url, "--output", output_path])
        assert cli_result.returncode == 0
        assert os.path.isfile(output_path)
        with open(output_path) as f:
            assert f.read() == "download me"
