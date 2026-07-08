#!/bin/sh
# POSIX sh (the prepare harness runs this with /bin/sh, not bash).
set -eu

# Workload-harness build for dbos-transact-py.
# Provisions a self-contained runtime: a Python env with the DBOS runtime deps
# + an embedded PostgreSQL (pgserver, which vendors PG 16 binaries), plus a
# no-op `uuid-ossp` shim so the DBOS system-DB migration's
# `CREATE EXTENSION "uuid-ossp"` succeeds (DBOS never calls uuid_generate_*; it
# uses the built-in gen_random_uuid()). DBOS itself is imported from the repo
# tree (the source under test), not pip-installed.
#
# Runtime entrypoint is `.workers/pyrun` (written below): it execs the resolved
# python so workload commands are stable regardless of venv availability.

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
echo "[build] repo root: $ROOT"

PY=$(command -v python3 || true)
if [ -z "$PY" ]; then
  echo "[build] FATAL: python3 not found"; exit 1
fi
echo "[build] python3: $PY"; "$PY" --version

VENV="$ROOT/.workers/venv"
PYEXE=""
if "$PY" -m venv "$VENV" >/dev/null 2>&1; then
  PYEXE="$VENV/bin/python3"
  "$PYEXE" -m pip install --quiet --upgrade pip || true
  echo "[build] using venv at $VENV"
else
  echo "[build] venv unavailable; installing into system/user site"
  PYEXE="$PY"
fi

pip_install() {
  # try plain, then --user, then --break-system-packages (PEP 668 images)
  "$PYEXE" -m pip install --quiet "$@" \
    || "$PYEXE" -m pip install --quiet --user "$@" \
    || "$PYEXE" -m pip install --quiet --break-system-packages "$@"
}

echo "[build] installing DBOS runtime deps + embedded postgres"
pip_install \
  "pyyaml>=6.0.2" \
  "python-dateutil>=2.9.0.post0" \
  "psycopg[binary]>=3.1" \
  "websockets>=14.0" \
  "typer-slim>=0.17.4" \
  "sqlalchemy>=2.0.43" \
  "pgserver"

echo "[build] locating pgserver extension dir + installing uuid-ossp shim"
EXTDIR=$("$PYEXE" -c 'import os,pgserver;print(os.path.join(os.path.dirname(pgserver.__file__),"pginstall","share","postgresql","extension"))')
echo "[build] extension dir: $EXTDIR"
if [ ! -f "$EXTDIR/uuid-ossp.control" ]; then
  printf "comment = 'no-op shim: DBOS uses built-in gen_random_uuid()'\ndefault_version = '1.1'\nrelocatable = true\n" > "$EXTDIR/uuid-ossp.control"
  printf -- "-- no-op shim; DBOS never calls uuid_generate_*, only built-in gen_random_uuid()\n" > "$EXTDIR/uuid-ossp--1.1.sql"
  echo "[build] installed uuid-ossp shim"
else
  echo "[build] uuid-ossp already present"
fi

echo "[build] writing .workers/pyrun launcher"
cat > "$ROOT/.workers/pyrun" <<PYRUN
#!/bin/sh
DIR=\$(CDPATH= cd "\$(dirname "\$0")" && pwd)
if [ -x "\$DIR/venv/bin/python3" ]; then
  exec "\$DIR/venv/bin/python3" "\$@"
fi
exec python3 "\$@"
PYRUN
chmod +x "$ROOT/.workers/pyrun"

echo "[build] smoke import (dbos from repo tree + pgserver)"
cd "$ROOT"
"$PYEXE" -c 'import sys; sys.path.insert(0,".");
import pgserver
import dbos
from dbos import DBOS, DBOSConfig, Queue
print("[build] dbos + pgserver import OK", dbos.__file__)'

echo "[build] done"
