"""Response metadata stripping for AI-friendly output.

ERPNext documents include verbose system metadata (owner, modified_by,
creation, etc.) and many null/empty fields. Stripping these reduces
token consumption when the output is consumed by AI agents.
"""

SYSTEM_FIELDS = frozenset({"owner", "modified_by", "creation", "modified", "idx", "doctype"})
ALWAYS_KEEP = frozenset({"name", "docstatus", "status"})


def _is_system_key(key: str) -> bool:
    return key.startswith("_") or key in SYSTEM_FIELDS


def _is_empty(value) -> bool:
    return value is None or value == ""


def strip_child_row(row: dict, fields: list[str] | None = None) -> dict:
    """Strip metadata from a single child table row."""
    allowed = frozenset([*fields, "name"]) if fields else None
    result = {}
    for key, value in row.items():
        if allowed and key not in allowed:
            continue
        if key != "name" and _is_system_key(key):
            continue
        if key != "name" and _is_empty(value):
            continue
        result[key] = value
    return result


def strip_document(
    doc: dict,
    fields: list[str] | None = None,
    child_fields: dict[str, list[str]] | None = None,
) -> dict:
    """Strip system metadata, null/empty values from an ERPNext document.

    Applies recursively to child tables (arrays of dicts).
    """
    child_field_keys = set(child_fields.keys()) if child_fields else set()
    allowed = frozenset([*fields, *ALWAYS_KEEP, *child_field_keys]) if fields else None
    result = {}

    for key, value in doc.items():
        if allowed and key not in allowed:
            continue
        if key not in ALWAYS_KEEP and _is_system_key(key):
            continue

        # Child tables: arrays of dicts
        if (
            isinstance(value, list)
            and value
            and isinstance(value[0], dict)
        ):
            cf = child_fields.get(key) if child_fields else None
            result[key] = [strip_child_row(row, cf) for row in value]
            continue

        if key not in ALWAYS_KEEP and _is_empty(value):
            continue

        result[key] = value

    return result
