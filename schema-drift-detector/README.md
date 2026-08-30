# Schema Drift Detector for Delta Lake

A lightweight agent that compares table schemas across pipeline runs, classifies
changes by downstream risk (`BREAKING` / `WARNING` / `ADDITIVE`), and can
generate a plain-English impact summary using Claude — designed to run as a
pre-merge or pre-promotion gate in a Databricks + Airflow environment.

## Why I built this

In production data platforms, silent schema drift is one of the most common
causes of pipeline failures that surface *downstream* rather than at the
source — a dropped column breaks a dashboard three hops away, or a type
change silently corrupts an ML feature pipeline weeks later. Most teams catch
this reactively, after something has already broken.

This project is a small, testable implementation of a pattern I've been
exploring for pre-release production testing: treat schema comparison as a
deterministic, unit-testable engine, and use an LLM only for the part that
actually benefits from language — explaining *why* a change matters and
*who* it will affect, in a form a human can act on in Slack or a PR comment.

The severity classification logic (safe type widening, nullability rules,
struct field nesting) reflects real patterns I've seen cause incidents in
Delta Lake / PySpark pipelines.

## What it does

Given a baseline schema and a current schema, it:

1. Flattens both (including nested struct fields) into comparable field maps
2. Diffs them and classifies every change:
   - **BREAKING** — dropped columns, incompatible type changes, nullable → NOT NULL
   - **WARNING** — safe type widening (e.g. `integer` → `long`), NOT NULL → nullable
   - **ADDITIVE** — new columns, safe for existing consumers
3. Outputs a structured JSON report
4. *(Optional)* Sends that report to Claude to generate a short, actionable
   Slack-ready summary with a promote / do-not-promote verdict
5. *(Optional)* Fails a CI/CD step (`--fail-on-breaking`) if any breaking
   change is detected — usable as an Airflow task or GitHub Actions gate
   before a canary rollout is promoted to production

## Quick start

```bash
git clone https://github.com/<your-username>/schema-drift-detector.git
cd schema-drift-detector
pip install -r requirements.txt   # only pytest is required for the core demo

# Compare the bundled sample schemas
python src/cli.py --baseline data/baseline_schema.json \
                   --current data/current_schema_breaking.json \
                   --table orders
```

Run the test suite:

```bash
python -m pytest tests/ -v
```

Generate a Claude-written impact summary (requires `ANTHROPIC_API_KEY`):

```bash
export ANTHROPIC_API_KEY=sk-...
python src/cli.py --baseline data/baseline_schema.json \
                   --current data/current_schema_breaking.json \
                   --table orders --with-summary
```

Gate a CI/CD pipeline on breaking changes:

```bash
python src/cli.py --baseline data/baseline_schema.json \
                   --current data/current_schema_breaking.json \
                   --fail-on-breaking   # exits 1 if any BREAKING change found
```

## Using it against a real Delta table

The diff engine is decoupled from Spark, but a thin integration is included
for real Delta Lake usage:

```python
from delta.tables import DeltaTable
from schema_diff import diff_from_delta_table

report = diff_from_delta_table(
    spark,
    table_path="/mnt/delta/orders",
    baseline_version=42,   # e.g. the version before today's job run
)
```

This is the shape I'd wire into an Airflow DAG as a task that runs
immediately after a canary write, before the rollout is promoted — see
[`ARCHITECTURE.md`](./ARCHITECTURE.md) for where this sits in a full
pipeline.

## What I'd add next (honest scope notes)

This is a focused proof of concept, not a production system. Things I'd add
before running this against a real pipeline at scale:

- **Column-level lineage awareness** — right now every downstream consumer
  is treated equally; a real system would weight severity by how many actual
  jobs/dashboards read the affected column (via Unity Catalog lineage APIs).
- **Historical drift tracking** — persist reports over time to catch slow,
  cumulative drift (e.g. a column's null rate creeping toward the nullability
  boundary) rather than only comparing two snapshots.
- **Configurable severity policy** — some teams may want type narrowing
  treated as WARNING not BREAKING for permissive tables; severity rules
  should be a config file, not hardcoded.
- **Enum/precision-level checks** — e.g. decimal precision/scale changes
  aren't currently modeled, only top-level type names.

## Tech stack

`Python` · `PySpark` / `Delta Lake` (optional integration) · `pytest` ·
`Claude API` · `GitHub Actions`

---

*Built as a portfolio project exploring AI-agent patterns for pre-release
production testing — pipeline validation, canary rollout monitoring, and
schema drift detection in Databricks/Airflow environments.*
