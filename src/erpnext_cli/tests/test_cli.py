"""CLI tests for erpnext-cli commands."""

import json
from unittest.mock import patch

from click.testing import CliRunner

from erpnext_cli.cli import cli

CONN = ["--json", "--url", "https://erp.example.com", "--api-key", "k", "--api-secret", "s"]


class TestDocumentDelete:
    def test_deletes_and_prints_confirmation(self):
        with patch("erpnext_cli.core.client.ERPNextClient._request", return_value={"message": "ok"}) as req:
            result = CliRunner().invoke(cli, CONN + ["document", "delete", "Item Price", "abc"])

        assert result.exit_code == 0, result.output
        req.assert_called_once_with("/api/resource/Item%20Price/abc", method="DELETE")
        assert json.loads(result.output) == {
            "status": "success",
            "doctype": "Item Price",
            "name": "abc",
        }
