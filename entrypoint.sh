#!/bin/sh
# Apply migrations, then serve.
#
# Nothing in this repo ran `alembic upgrade head` against the deployed
# database, so schema changes were applied by hand and a deploy could ship
# code expecting a column that did not exist yet. Running migrations here
# means the schema is always at least as new as the code that needs it.
#
# set -e matters: if the migration fails, the container must exit rather than
# start serving against a schema it does not match. A crash-looping deploy is
# recoverable and obvious; one silently serving 500s from a half-migrated
# database is neither.
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting server on port ${PORT:-8000}..."
# Railway assigns a port at runtime; default to 8000 for local use.
exec uvicorn moat.main:app --host 0.0.0.0 --port "${PORT:-8000}"
