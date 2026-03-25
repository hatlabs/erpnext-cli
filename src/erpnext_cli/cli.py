"""ERPNext CLI — Click-based command interface with REPL."""

import json
import sys

import click

from erpnext_cli.core.client import ERPNextAPIError, make_client
from erpnext_cli.core import documents, files, methods, reports, schema
from erpnext_cli.core.strip import strip_document


class CliContext:
    """Shared state passed via Click context."""

    def __init__(
        self,
        json_output: bool,
        raw_output: bool,
        url: str | None,
        api_key: str | None,
        api_secret: str | None,
    ):
        self.json_output = json_output
        self.raw_output = raw_output
        self.url = url
        self.api_key = api_key
        self.api_secret = api_secret
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = make_client(
                url=self.url, api_key=self.api_key, api_secret=self.api_secret
            )
        return self._client


pass_ctx = click.make_pass_decorator(CliContext, ensure=True)


def _output(ctx: CliContext, data) -> None:
    """Print data as JSON (always — ERPNext data is inherently structured)."""
    click.echo(json.dumps(data, indent=2, default=str))


def _output_list(ctx: CliContext, data: list[dict], headers: list[str], row_fn) -> None:
    """Print a list of documents as JSON or table."""
    if ctx.json_output:
        click.echo(json.dumps(data, indent=2, default=str))
        return

    from erpnext_cli.utils.repl_skin import ReplSkin

    skin = ReplSkin("erpnext")
    rows = [row_fn(d) for d in data]
    skin.table(headers, rows)
    skin.hint(f"\n  {len(data)} result(s)")


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.option("--json", "json_output", is_flag=True, help="Output as JSON.")
@click.option("--raw", "raw_output", is_flag=True, help="Disable response stripping.")
@click.option("--url", envvar="ERPNEXT_URL", help="ERPNext URL.")
@click.option("--api-key", envvar="ERPNEXT_API_KEY", help="ERPNext API key.")
@click.option("--api-secret", envvar="ERPNEXT_API_SECRET", help="ERPNext API secret.")
@click.pass_context
def cli(ctx, json_output, raw_output, url, api_key, api_secret):
    """ERPNext CLI — command-line interface to ERPNext REST/RPC API."""
    ctx.obj = CliContext(
        json_output=json_output,
        raw_output=raw_output,
        url=url,
        api_key=api_key,
        api_secret=api_secret,
    )

    if ctx.invoked_subcommand is None:
        _run_repl(ctx.obj)


def _run_repl(ctx: CliContext) -> None:
    """Interactive REPL mode."""
    from erpnext_cli.utils.repl_skin import ReplSkin

    skin = ReplSkin("erpnext")
    skin.print_banner()

    pt_session = skin.create_prompt_session()

    commands = {
        "document list <DocType>": "List documents",
        "document get <DocType> <name>": "Get single document",
        "document create <DocType> -d JSON": "Create document",
        "document update <DocType> <name> -d JSON": "Update document",
        "document submit <DocType> <name>": "Submit document",
        "document cancel <DocType> <name>": "Cancel document",
        "document children <parent> <child>": "Query child table rows",
        "schema doctypes": "List all DocTypes",
        "schema fields <DocType>": "Show DocType fields",
        "report run <name>": "Run a saved report",
        "file upload <path> [--doctype DT --docname DN]": "Upload a file",
        "file list <DocType> <name>": "List attachments",
        "file download <file-url> [--output PATH]": "Download a file",
        "method call <path>": "Call a server method",
        "help": "Show this help",
        "quit / exit": "Exit the REPL",
    }

    while True:
        try:
            line = skin.get_input(pt_session)
        except (EOFError, KeyboardInterrupt):
            skin.print_goodbye()
            break

        if not line:
            continue

        if line in ("quit", "exit", "q"):
            skin.print_goodbye()
            break

        if line == "help":
            skin.help(commands)
            continue

        # Parse and dispatch via Click
        args = _split_args(line)
        if ctx.json_output:
            args = ["--json"] + args
        if ctx.raw_output:
            args = ["--raw"] + args

        try:
            cli.main(args=args, standalone_mode=False, obj=ctx)
        except SystemExit:
            pass
        except ERPNextAPIError as e:
            skin.error(str(e))
        except click.UsageError as e:
            skin.error(str(e))
        except Exception as e:
            skin.error(f"Unexpected error: {e}")


def _split_args(line: str) -> list[str]:
    """Split a REPL input line respecting quoted strings."""
    import shlex

    try:
        return shlex.split(line)
    except ValueError:
        return line.split()


# ---------------------------------------------------------------------------
# document
# ---------------------------------------------------------------------------


@cli.group(name="document")
def document_cmd():
    """Document CRUD operations."""


@document_cmd.command("list")
@click.argument("doctype")
@click.option("--filters", "-f", default=None, help="JSON filter object.")
@click.option("--fields", default=None, help="JSON array of field names.")
@click.option("--limit", "-l", default=None, type=int, help="Max results (default: 20).")
@pass_ctx
def document_list(ctx, doctype, filters, fields, limit):
    """List documents of a DocType."""
    f = json.loads(filters) if filters else None
    flds = json.loads(fields) if fields else None

    data = documents.list_documents(ctx.client, doctype, filters=f, fields=flds, limit=limit)

    if ctx.json_output:
        click.echo(json.dumps(data, indent=2, default=str))
        return

    if not data:
        click.echo("No results.")
        return

    # Auto-detect columns from first result
    headers = list(data[0].keys())
    from erpnext_cli.utils.repl_skin import ReplSkin

    skin = ReplSkin("erpnext")
    rows = [[str(d.get(h, "")) for h in headers] for d in data]
    skin.table(headers, rows)
    skin.hint(f"\n  {len(data)} result(s)")


@document_cmd.command("get")
@click.argument("doctype")
@click.argument("name")
@click.option("--fields", default=None, help="JSON array of top-level field names.")
@click.option("--child-fields", default=None, help='JSON object: {"table_field": ["col1", "col2"]}.')
@pass_ctx
def document_get(ctx, doctype, name, fields, child_fields):
    """Get a single document."""
    doc = documents.get_document(ctx.client, doctype, name)

    if not ctx.raw_output:
        flds = json.loads(fields) if fields else None
        cflds = json.loads(child_fields) if child_fields else None
        doc = strip_document(doc, fields=flds, child_fields=cflds)

    _output(ctx, doc)


@document_cmd.command("create")
@click.argument("doctype")
@click.option("--data", "-d", required=True, help="JSON document data.")
@pass_ctx
def document_create(ctx, doctype, data):
    """Create a new document."""
    doc_data = json.loads(data)
    result = documents.create_document(ctx.client, doctype, doc_data)
    _output(ctx, result)


@document_cmd.command("update")
@click.argument("doctype")
@click.argument("name")
@click.option("--data", "-d", required=True, help="JSON data to update.")
@pass_ctx
def document_update(ctx, doctype, name, data):
    """Update an existing document."""
    doc_data = json.loads(data)
    result = documents.update_document(ctx.client, doctype, name, doc_data)
    _output(ctx, result)


@document_cmd.command("submit")
@click.argument("doctype")
@click.argument("name")
@pass_ctx
def document_submit(ctx, doctype, name):
    """Submit a document (set docstatus=1). Irreversible."""
    result = documents.submit_document(ctx.client, doctype, name)
    _output(ctx, result)


@document_cmd.command("cancel")
@click.argument("doctype")
@click.argument("name")
@pass_ctx
def document_cancel(ctx, doctype, name):
    """Cancel a submitted document (set docstatus=2)."""
    result = documents.cancel_document(ctx.client, doctype, name)
    _output(ctx, result)


@document_cmd.command("children")
@click.argument("parent_doctype")
@click.argument("child_doctype")
@click.option("--parent-fields", default=None, help='JSON array (default: ["name"]).')
@click.option("--child-fields", default=None, help="JSON array of child table fields.")
@click.option("--child-filters", default=None, help='JSON array of [field, op, value] triples.')
@click.option("--parent-filters", default=None, help="JSON filter object for parent.")
@click.option("--limit", "-l", default=None, type=int, help="Max results (default: 100).")
@pass_ctx
def document_children(
    ctx, parent_doctype, child_doctype, parent_fields, child_fields,
    child_filters, parent_filters, limit,
):
    """Query child table rows via parent-child join."""
    pf = json.loads(parent_fields) if parent_fields else None
    cf = json.loads(child_fields) if child_fields else None
    cflt = json.loads(child_filters) if child_filters else None
    pflt = json.loads(parent_filters) if parent_filters else None

    data = documents.get_child_documents(
        ctx.client,
        parent_doctype,
        child_doctype,
        parent_fields=pf,
        child_fields=cf,
        child_filters=cflt,
        parent_filters=pflt,
        limit=limit,
    )
    _output(ctx, data)


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


@cli.group(name="schema")
def schema_cmd():
    """DocType schema introspection."""


@schema_cmd.command("doctypes")
@click.option("--limit", "-l", default=500, type=int, help="Max results.")
@pass_ctx
def schema_doctypes(ctx, limit):
    """List all available DocTypes."""
    data = schema.list_doctypes(ctx.client, limit=limit)

    if ctx.json_output:
        click.echo(json.dumps(data, indent=2, default=str))
        return

    from erpnext_cli.utils.repl_skin import ReplSkin

    skin = ReplSkin("erpnext")
    rows = [[name] for name in sorted(data)]
    skin.table(["DocType"], rows)
    skin.hint(f"\n  {len(data)} DocType(s)")


@schema_cmd.command("fields")
@click.argument("doctype")
@pass_ctx
def schema_fields(ctx, doctype):
    """Show field definitions for a DocType."""
    data = schema.get_doctype_fields(ctx.client, doctype)

    if ctx.json_output:
        click.echo(json.dumps(data, indent=2, default=str))
        return

    from erpnext_cli.utils.repl_skin import ReplSkin

    skin = ReplSkin("erpnext")
    rows = [
        [
            f["fieldname"],
            f["fieldtype"],
            f["label"],
            f["options"] or "",
            "Yes" if f["reqd"] else "",
        ]
        for f in data
    ]
    skin.table(["Field", "Type", "Label", "Options", "Required"], rows)
    skin.hint(f"\n  {len(data)} field(s)")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


@cli.group(name="report")
def report_cmd():
    """Report execution."""


@report_cmd.command("run")
@click.argument("report_name")
@click.option("--filters", "-f", default=None, help="JSON filter object.")
@pass_ctx
def report_run(ctx, report_name, filters):
    """Run an ERPNext saved report."""
    f = json.loads(filters) if filters else None
    data = reports.run_report(ctx.client, report_name, filters=f)
    _output(ctx, data)


# ---------------------------------------------------------------------------
# method
# ---------------------------------------------------------------------------


@cli.group(name="method")
def method_cmd():
    """Whitelisted server method calls."""


@method_cmd.command("call")
@click.argument("method_path")
@click.option("--args", "-a", "method_args", default=None, help="JSON args object.")
@click.option(
    "--http-method", "-m", default="POST",
    type=click.Choice(["GET", "POST"], case_sensitive=False),
    help="HTTP method (default: POST).",
)
@pass_ctx
def method_call(ctx, method_path, method_args, http_method):
    """Call a whitelisted Frappe/ERPNext method."""
    args = json.loads(method_args) if method_args else None
    data = methods.call_method(
        ctx.client, method_path, args=args, http_method=http_method.upper()
    )
    _output(ctx, data)


# ---------------------------------------------------------------------------
# file
# ---------------------------------------------------------------------------


@cli.group(name="file")
def file_cmd():
    """File upload, download, and attachment operations."""


@file_cmd.command("upload")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--doctype", default=None, help="Attach to this DocType.")
@click.option("--docname", default=None, help="Attach to this document name.")
@click.option("--field", default=None, help="Set this field on the document to the file URL.")
@click.option("--public", "is_public", is_flag=True, help="Upload as public (default: private).")
@pass_ctx
def file_upload(ctx, file_path, doctype, docname, field, is_public):
    """Upload a local file to ERPNext."""
    result = files.upload_file(
        ctx.client, file_path,
        doctype=doctype, docname=docname, is_private=not is_public,
        field=field,
    )
    _output(ctx, result)


@file_cmd.command("list")
@click.argument("doctype")
@click.argument("docname")
@pass_ctx
def file_list(ctx, doctype, docname):
    """List files attached to a document."""
    data = files.list_attachments(ctx.client, doctype, docname)

    if ctx.json_output:
        click.echo(json.dumps(data, indent=2, default=str))
        return

    if not data:
        click.echo("No attachments.")
        return

    from erpnext_cli.utils.repl_skin import ReplSkin

    skin = ReplSkin("erpnext")
    headers = ["name", "file_name", "file_url", "is_private", "file_size"]
    rows = [[str(d.get(h, "")) for h in headers] for d in data]
    skin.table(headers, rows)
    skin.hint(f"\n  {len(data)} attachment(s)")


@file_cmd.command("download")
@click.argument("file_url")
@click.option("--output", "-o", "output_path", default=None, help="Destination file path.")
@pass_ctx
def file_download(ctx, file_url, output_path):
    """Download a file by its file_url."""
    import os

    data, content_type = files.download_file(ctx.client, file_url)

    if not output_path:
        output_path = os.path.basename(file_url)

    with open(output_path, "wb") as f:
        f.write(data)

    click.echo(f"Downloaded to {output_path} ({len(data)} bytes)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    try:
        cli(standalone_mode=True)
    except ERPNextAPIError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
