import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from schema_diff import Severity, diff_schemas


def schema(fields):
    return {"fields": fields}


def test_no_changes_produces_empty_report():
    s = schema([{"name": "id", "type": "long", "nullable": False}])
    report = diff_schemas(s, s, table_name="t")
    assert report.changes == []
    assert not report.has_breaking_changes


def test_dropped_column_is_breaking():
    baseline = schema([
        {"name": "id", "type": "long", "nullable": False},
        {"name": "email", "type": "string", "nullable": True},
    ])
    current = schema([
        {"name": "id", "type": "long", "nullable": False},
    ])
    report = diff_schemas(baseline, current, table_name="t")
    assert report.has_breaking_changes
    dropped = [c for c in report.changes if c.change_type == "dropped"]
    assert len(dropped) == 1
    assert dropped[0].field_name == "email"
    assert dropped[0].severity == Severity.BREAKING


def test_added_column_is_additive():
    baseline = schema([{"name": "id", "type": "long", "nullable": False}])
    current = schema([
        {"name": "id", "type": "long", "nullable": False},
        {"name": "new_field", "type": "string", "nullable": True},
    ])
    report = diff_schemas(baseline, current, table_name="t")
    assert not report.has_breaking_changes
    added = [c for c in report.changes if c.change_type == "added"]
    assert len(added) == 1
    assert added[0].severity == Severity.ADDITIVE


def test_incompatible_type_change_is_breaking():
    baseline = schema([{"name": "status", "type": "string", "nullable": False}])
    current = schema([{"name": "status", "type": "integer", "nullable": False}])
    report = diff_schemas(baseline, current, table_name="t")
    assert report.has_breaking_changes


def test_safe_type_widening_is_warning_not_breaking():
    baseline = schema([{"name": "count", "type": "integer", "nullable": True}])
    current = schema([{"name": "count", "type": "long", "nullable": True}])
    report = diff_schemas(baseline, current, table_name="t")
    assert not report.has_breaking_changes
    assert report.has_warnings


def test_nullable_to_not_null_is_breaking():
    baseline = schema([{"name": "amount", "type": "double", "nullable": True}])
    current = schema([{"name": "amount", "type": "double", "nullable": False}])
    report = diff_schemas(baseline, current, table_name="t")
    assert report.has_breaking_changes


def test_not_null_to_nullable_is_warning():
    baseline = schema([{"name": "amount", "type": "double", "nullable": False}])
    current = schema([{"name": "amount", "type": "double", "nullable": True}])
    report = diff_schemas(baseline, current, table_name="t")
    assert not report.has_breaking_changes
    assert report.has_warnings


def test_nested_struct_field_dropped_is_detected():
    baseline = schema([
        {
            "name": "address",
            "type": "struct",
            "nullable": True,
            "fields": [
                {"name": "city", "type": "string", "nullable": True},
                {"name": "zip", "type": "string", "nullable": True},
            ],
        }
    ])
    current = schema([
        {
            "name": "address",
            "type": "struct",
            "nullable": True,
            "fields": [
                {"name": "city", "type": "string", "nullable": True},
            ],
        }
    ])
    report = diff_schemas(baseline, current, table_name="t")
    assert report.has_breaking_changes
    assert any(c.field_name == "address.zip" for c in report.changes)


def test_sample_breaking_fixture_end_to_end():
    data_dir = Path(__file__).parent.parent / "data"
    baseline = json.loads((data_dir / "baseline_schema.json").read_text())
    current = json.loads((data_dir / "current_schema_breaking.json").read_text())
    report = diff_schemas(baseline, current, table_name="orders")

    assert report.has_breaking_changes
    breaking_fields = {c.field_name for c in report.changes if c.severity == Severity.BREAKING}
    assert "discount_code" in breaking_fields          # dropped
    assert "order_status" in breaking_fields           # incompatible type change
    assert "order_amount" in breaking_fields           # nullable -> not null


def test_sample_additive_fixture_has_no_breaking_changes():
    data_dir = Path(__file__).parent.parent / "data"
    baseline = json.loads((data_dir / "baseline_schema.json").read_text())
    current = json.loads((data_dir / "current_schema_additive.json").read_text())
    report = diff_schemas(baseline, current, table_name="orders")

    assert not report.has_breaking_changes
