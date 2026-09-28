#!/bin/sh
# Runs once per container start. create_tables.py is idempotent (see its
# own docstring) — safe to run on every boot, and it's what stands in for
# Alembic until that gets set up. If a table's SHAPE changes later
# (renamed/dropped column, new constraint on existing data) this script
# won't pick it up — only genuinely new tables.
set -e

echo "Ensuring database tables exist..."
python -m app.create_tables

echo "Starting API server..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
