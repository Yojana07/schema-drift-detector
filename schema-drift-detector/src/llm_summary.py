"""
llm_summary.py

Turns a structured DriftReport into a human-readable, downstream-impact
summary using the Claude API. This is the "agent" layer on top of the
deterministic schema_diff engine: the diff logic decides WHAT changed,
Claude explains WHY it matters and WHAT to do about it in plain English
for a Slack message or PR comment.

Requires: ANTHROPIC_API_KEY environment variable.
Install:  pip install anthropic
"""

from __future__ import annotations

import os
from schema_diff import DriftReport

try:
    import anthropic
except ImportError:  # pragma: no cover
    anthropic = None


SYSTEM_PROMPT = """You are a data platform assistant. You will be given a JSON
schema-drift report comparing a baseline and current version of a Delta Lake
table. Write a concise, actionable summary for a data engineering Slack channel.

Rules:
- Lead with a one-line verdict: SAFE TO PROMOTE, PROMOTE WITH CAUTION, or DO NOT PROMOTE.
- If there are BREAKING changes, list each one with which downstream consumers
  are likely affected (assume consumers are: BI dashboards, ML feature pipelines,
  and downstream Airflow DAGs reading this table).
- Keep WARNING items to one line each.
- Skip ADDITIVE changes unless there are more than 5 of them (then just count them).
- Total response under 150 words. No preamble, no sign-off.
"""


def generate_summary(report: DriftReport, model: str = "claude-sonnet-4-5") -> str:
    if anthropic is None:
        raise RuntimeError("The 'anthropic' package is not installed. Run: pip install anthropic")

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("Set the ANTHROPIC_API_KEY environment variable before calling generate_summary().")

    client = anthropic.Anthropic(api_key=api_key)

    response = client.messages.create(
        model=model,
        max_tokens=400,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": report.to_json()}
        ],
    )

    return "".join(block.text for block in response.content if block.type == "text")


if __name__ == "__main__":
    # Small manual smoke test — run against the sample breaking-change data.
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent))
    from schema_diff import diff_schemas, load_json_schema

    data_dir = Path(__file__).parent.parent / "data"
    baseline = load_json_schema(data_dir / "baseline_schema.json")
    current = load_json_schema(data_dir / "current_schema_breaking.json")

    report = diff_schemas(baseline, current, table_name="orders")
    print(report.to_json())
    print("\n--- Claude summary ---\n")
    print(generate_summary(report))
