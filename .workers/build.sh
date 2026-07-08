#!/usr/bin/env bash
set -euo pipefail

# Workload-harness build for dbos-transact-py.
# Provisions a self-contained runtime image: a venv with the DBOS runtime
# dependencies + an embedded PostgreSQL (pgserver, which vendors PG 16
# binaries), plus a no-op `uuid-ossp` shim so the DBOS system-DB migration's
# `CREATE EXTENSION "uuid-ossp"` succeeds. DBOS never calls uuid_generate_*
# (it uses the built-in gen_random_uuid()), so an empty shim is faithful.
#
# The DBOS package itself is imported from the repo tree via PYTHONPATH at
# runtime (the source under test), NOT pip-installed — we test THIS checkout.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/.workers/venv"

echo "[build] repo root: $ROOT"
PYBIN="$(command -v python3)"
echo "[build] python3: $PYBIN"; "$PYBIN" --version

echo "[build] creating venv at $VENV"
"$PYBIN" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip

echo "[build] installing DBOS runtime deps + embedded postgres"
"$VENV/bin/pip" install --quiet \
  "pyyaml>=6.0.2" \
  "python-dateutil>=2.9.0.post0" \
  "psycopg[binary]>=3.1" \
  "websockets>=14.0" \
  "typer-slim>=0.17.4" \
  "sqlalchemy>=2.0.43" \
  "pgserver"

# Locate pgserver's bundled postgres extension dir and install the uuid-ossp shim.
EXTDIR="$("$VENV/bin/python3" - <<'PY'
import os, pgserver
base = os.path.dirname(pgserver.__file__)
print(os.path.join(base, "pginstall", "share", "postgresql", "extension"))
PY
)"
echo "[build] pgserver extension dir: $EXTDIR"
if [ ! -f "$EXTDIR/uuid-ossp.control" ]; then
  cat > "$EXTDIR/uuid-ossp.control" <<'CTL'
comment = 'no-op shim: DBOS uses built-in gen_random_uuid()'
default_version = '1.1'
relocatable = true
CTL
  cat > "$EXTDIR/uuid-ossp--1.1.sql" <<'SQL'
-- no-op shim; DBOS never calls uuid_generate_*, only built-in gen_random_uuid()
SQL
  echo "[build] installed uuid-ossp shim"
else
  echo "[build] uuid-ossp already present"
fi

# Smoke: prove dbos imports from the repo tree and pgserver is present.
echo "[build] smoke import"
PYTHONPATH="$ROOT" "$VENV/bin/python3" - <<'PY'
import dbos, pgserver
from dbos import DBOS, DBOSConfig, Queue
print("[build] dbos + pgserver import OK")
PY

echo "[build] done"
