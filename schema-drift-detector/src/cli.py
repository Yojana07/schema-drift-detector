"""
cli.py

Command-line interface for schema-drift-detector.

Examples
--------
Compare two local JSON schema snapshots:
    python -m src.cli --baseline data/baseline_schema.json \\
                       --current data/current_schema_breaking.json \\
                       --table orders

Compare two Delta Lake table versions (requires PySpark + delta-spark):
    python -m src.cli --delta-path /mnt/delta/orders \\
                       --baseline-version 10 --current-version 12

Add --with-summary to also generate a Claude-written impact summary
(requires ANTHROPIC_API_KEY to be set).
"""

from __future__ import annotations

import argparse
import sys

from schema_diff import diff_schemas, load_json_schema


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detect schema drift between two table schema snapshots.")
    parser.add_argument("--baseline", help="Path to baseline schema JSON file")
    parser.add_argument("--current", help="Path to current schema JSON file")
    parser.add_argument("--table", default="unnamed_table", help="Table name, for reporting purposes")
    parser.add_argument("--delta-path", help="Path to a Delta table (uses Spark + delta-spark instead of JSON files)")
    parser.add_argument("--baseline-version", type=int, help="Delta table version to use as baseline")
    parser.add_argument("--current-version", type=int, help="Delta table version to use as current (default: latest)")
    parser.add_argument("--with-summary", action="store_true", help="Also generate a Claude-written impact summary")
    parser.add_argument("--fail-on-breaking", action="store_true", help="Exit with code 1 if any BREAKING change is found (for CI gating)")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.delta_path:
        from schema_diff import diff_from_delta_table
        from pyspark.sql import SparkSession

        spark = SparkSession.builder.appName("schema-drift-detector").getOrCreate()
        report = diff_from_delta_table(
            spark,
            args.delta_path,
            baseline_version=args.baseline_version,
            current_version=args.current_version,
        )
    elif args.baseline and args.current:
        baseline = load_json_schema(args.baseline)
        current = load_json_schema(args.current)
        report = diff_schemas(baseline, current, table_name=args.table)
    else:
        print("Error: provide either --baseline/--current JSON files, or --delta-path with versions.", file=sys.stderr)
        return 2

    print(report.to_json())

    if args.with_summary:
        from llm_summary import generate_summary
        print("\n--- Claude impact summary ---\n")
        print(generate_summary(report))

    if args.fail_on_breaking and report.has_breaking_changes:
        print("\nBREAKING changes detected — failing build.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
