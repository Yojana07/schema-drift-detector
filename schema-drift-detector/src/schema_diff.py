"""
schema_diff.py

Compares two table schemas (baseline vs. current) and classifies the
differences by severity. Designed to work against:
  - Delta Lake table schemas (via delta-spark, if available)
  - Plain JSON schema snapshots (for local dev / CI / demos without Spark)

Severity model
--------------
BREAKING   : a change that will very likely break downstream consumers
             (column dropped, type narrowed/changed incompatibly, column
             made non-nullable when it previously allowed nulls)
WARNING    : a change that *could* break something depending on how strict
             downstream consumers are (column reordered, type widened,
             nullability relaxed)
ADDITIVE   : a change that is safe for existing consumers
             (new column added, new nested field added)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    BREAKING = "BREAKING"
    WARNING = "WARNING"
    ADDITIVE = "ADDITIVE"


# Type promotion rules: widening a type is safe, narrowing is not.
# Keyed as (from_type, to_type) -> is_safe_widening
SAFE_WIDENING = {
    ("integer", "long"),
    ("integer", "double"),
    ("long", "double"),
    ("float", "double"),
    ("short", "integer"),
    ("byte", "short"),
    ("date", "timestamp"),
}


@dataclass
class SchemaChange:
    field_name: str
    change_type: str  # "dropped", "added", "type_changed", "nullability_changed"
    severity: Severity
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field_name,
            "change_type": self.change_type,
            "severity": self.severity.value,
            "detail": self.detail,
        }


@dataclass
class DriftReport:
    table_name: str
    changes: list[SchemaChange] = field(default_factory=list)

    @property
    def has_breaking_changes(self) -> bool:
        return any(c.severity == Severity.BREAKING for c in self.changes)

    @property
    def has_warnings(self) -> bool:
        return any(c.severity == Severity.WARNING for c in self.changes)

    def summary_counts(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Severity}
        for c in self.changes:
            counts[c.severity.value] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table_name,
            "has_breaking_changes": self.has_breaking_changes,
            "summary": self.summary_counts(),
            "changes": [c.to_dict() for c in self.changes],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def _flatten_fields(schema: dict[str, Any], prefix: str = "") -> dict[str, dict[str, Any]]:
    """
    Flattens a JSON-represented schema (list of {name, type, nullable, fields?})
    into a dotted-path dict, so nested struct fields are comparable too.

    Expected schema shape:
    {
        "fields": [
            {"name": "id", "type": "long", "nullable": false},
            {"name": "address", "type": "struct", "nullable": true,
             "fields": [{"name": "city", "type": "string", "nullable": true}]}
        ]
    }
    """
    flat: dict[str, dict[str, Any]] = {}
    for f in schema.get("fields", []):
        full_name = f"{prefix}{f['name']}"
        flat[full_name] = {"type": f["type"], "nullable": f.get("nullable", True)}
        if f["type"] == "struct" and "fields" in f:
            flat.update(_flatten_fields(f, prefix=f"{full_name}."))
    return flat


def diff_schemas(baseline: dict[str, Any], current: dict[str, Any], table_name: str) -> DriftReport:
    """
    Core comparison. Pure function, no I/O, fully unit-testable.
    """
    base_fields = _flatten_fields(baseline)
    curr_fields = _flatten_fields(current)

    report = DriftReport(table_name=table_name)

    # Dropped columns -> BREAKING
    for name in base_fields.keys() - curr_fields.keys():
        report.changes.append(
            SchemaChange(
                field_name=name,
                change_type="dropped",
                severity=Severity.BREAKING,
                detail=f"Column '{name}' existed in baseline but is missing in current schema.",
            )
        )

    # Added columns -> ADDITIVE
    for name in curr_fields.keys() - base_fields.keys():
        report.changes.append(
            SchemaChange(
                field_name=name,
                change_type="added",
                severity=Severity.ADDITIVE,
                detail=f"New column '{name}' added ({curr_fields[name]['type']}).",
            )
        )

    # Fields present in both -> check type + nullability changes
    for name in base_fields.keys() & curr_fields.keys():
        base_type = base_fields[name]["type"]
        curr_type = curr_fields[name]["type"]
        base_null = base_fields[name]["nullable"]
        curr_null = curr_fields[name]["nullable"]

        if base_type != curr_type:
            if (base_type, curr_type) in SAFE_WIDENING:
                severity = Severity.WARNING
                detail = f"Type widened from {base_type} to {curr_type} (generally safe, but re-verify downstream casts)."
            else:
                severity = Severity.BREAKING
                detail = f"Type changed from {base_type} to {curr_type} (not a recognized safe widening)."
            report.changes.append(
                SchemaChange(field_name=name, change_type="type_changed", severity=severity, detail=detail)
            )

        if base_null and not curr_null:
            report.changes.append(
                SchemaChange(
                    field_name=name,
                    change_type="nullability_changed",
                    severity=Severity.BREAKING,
                    detail=f"Column '{name}' was nullable, now NOT NULL. Existing null values will break writes/reads.",
                )
            )
        elif not base_null and curr_null:
            report.changes.append(
                SchemaChange(
                    field_name=name,
                    change_type="nullability_changed",
                    severity=Severity.WARNING,
                    detail=f"Column '{name}' relaxed from NOT NULL to nullable. Downstream code assuming non-null may break.",
                )
            )

    return report


def load_json_schema(path: str) -> dict[str, Any]:
    with open(path, "r") as f:
        return json.load(f)


def diff_from_delta_table(spark, table_path: str, baseline_version: int, current_version: "int | None" = None):
    """
    Optional Delta Lake integration. Requires delta-spark to be installed
    and a live SparkSession. Not required for the JSON-based demo/tests.

    Usage:
        from delta.tables import DeltaTable
        report = diff_from_delta_table(spark, "/mnt/delta/orders", baseline_version=10)
    """
    def _spark_schema_to_json(struct_type) -> dict[str, Any]:
        return json.loads(struct_type.json())

    baseline_df = spark.read.format("delta").option("versionAsOf", baseline_version).load(table_path)
    if current_version is not None:
        current_df = spark.read.format("delta").option("versionAsOf", current_version).load(table_path)
    else:
        current_df = spark.read.format("delta").load(table_path)  # latest

    baseline_schema = _spark_schema_to_json(baseline_df.schema)
    current_schema = _spark_schema_to_json(current_df.schema)

    return diff_schemas(baseline_schema, current_schema, table_name=table_path)
