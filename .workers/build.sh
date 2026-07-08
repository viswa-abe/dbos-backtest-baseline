#!/bin/sh
# POSIX sh (prepare runs this with /bin/sh). Diagnostic + provisioning build.
# All progress goes to stderr (that is what the prepare log preview captures).
# We do NOT use `set -e`: every step reports its own rc so a failure is legible
# in the build log instead of aborting silently.

log() { echo "[build] $*" >&2; }

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
log "repo root: $ROOT"
log "uname: $(uname -a 2>&1)"
log "id: $(id 2>&1)"
log "shell: $0"

PY=$(command -v python3 || true)
log "python3: ${PY:-MISSING}"
if [ -z "$PY" ]; then
  log "FATAL python3 not found; PATH=$PATH"
  log "ls /usr/bin/python*: $(ls /usr/bin/python* 2>&1)"
  exit 1
fi
log "python version: $("$PY" --version 2>&1)"
"$PY" -m pip --version >&2 2>&1; log "pip rc=$?"

VENV="$ROOT/.workers/venv"
PYEXE="$PY"
if "$PY" -m venv "$VENV" >&2 2>&1; then
  PYEXE="$VENV/bin/python3"
  "$PYEXE" -m pip install --upgrade pip >&2 2>&1; log "venv pip upgrade rc=$?"
  log "using venv python: $PYEXE"
else
  log "venv unavailable (rc=$?); using system python: $PYEXE"
fi

log "pip install deps + pgserver (this needs build-time network)"
"$PYEXE" -m pip install \
  "pyyaml>=6.0.2" "python-dateutil>=2.9.0.post0" "psycopg[binary]>=3.1" \
  "websockets>=14.0" "typer-slim>=0.17.4" "sqlalchemy>=2.0.43" "pgserver" >&2 2>&1
RC=$?
log "pip install rc=$RC"
if [ "$RC" -ne 0 ]; then
  log "retry with --break-system-packages"
  "$PYEXE" -m pip install --break-system-packages \
    "pyyaml>=6.0.2" "python-dateutil>=2.9.0.post0" "psycopg[binary]>=3.1" \
    "websockets>=14.0" "typer-slim>=0.17.4" "sqlalchemy>=2.0.43" "pgserver" >&2 2>&1
  RC=$?
  log "pip install (break-system) rc=$RC"
fi
if [ "$RC" -ne 0 ]; then
  log "FATAL pip install failed"
  exit 1
fi

log "installing uuid-ossp shim"
EXTDIR=$("$PYEXE" -c 'import os,pgserver;print(os.path.join(os.path.dirname(pgserver.__file__),"pginstall","share","postgresql","extension"))' 2>>/dev/stderr)
log "extension dir: $EXTDIR"
if [ -n "$EXTDIR" ] && [ ! -f "$EXTDIR/uuid-ossp.control" ]; then
  printf "comment = 'no-op shim'\ndefault_version = '1.1'\nrelocatable = true\n" > "$EXTDIR/uuid-ossp.control"
  printf -- "-- no-op shim; DBOS uses built-in gen_random_uuid()\n" > "$EXTDIR/uuid-ossp--1.1.sql"
  log "shim installed"
fi

log "writing .workers/pyrun launcher"
cat > "$ROOT/.workers/pyrun" <<'PYRUN'
#!/bin/sh
DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
if [ -x "$DIR/venv/bin/python3" ]; then exec "$DIR/venv/bin/python3" "$@"; fi
exec python3 "$@"
PYRUN
chmod +x "$ROOT/.workers/pyrun"

log "smoke import (dbos from repo tree + pgserver)"
cd "$ROOT"
"$PYEXE" -c 'import sys; sys.path.insert(0,".")
import pgserver, dbos
from dbos import DBOS, DBOSConfig, Queue
print("[build] dbos+pgserver import OK", dbos.__file__)' >&2 2>&1
RC=$?
log "smoke rc=$RC"
[ "$RC" -eq 0 ] || { log "FATAL smoke import failed"; exit 1; }
log "done OK"
