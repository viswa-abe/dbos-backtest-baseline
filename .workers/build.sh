#!/bin/sh
# POSIX sh (prepare runs this with /bin/sh, as root). Provisioning build.
#
# Build env vs runtime env (learned by probing):
#   BUILD   = Ubuntu Noble, glibc, apt, root, python3.12 as `python3`.
#   RUNTIME = musl + gcompat. glibc C-extension wheels DO NOT LOAD at runtime
#             (`mallinfo` symbol not found). Only pure-Python / stdlib survives.
#
# DBOS is exercised on its first-class **SQLite** system-DB backend: stdlib
# `sqlite3` (compiled into the python binary) + pure-Python SQLAlchemy. No
# Postgres server, no psycopg C driver, no pgserver — all of which are glibc
# wheels that break on the musl runtime.
#
# `import dbos` eagerly does `import psycopg` (dbos/_utils.py:6, _queue.py:6),
# but only for Postgres error-classification that never runs on SQLite. So we
# install a tiny PURE-PYTHON `psycopg` shim that merely satisfies the import
# (OperationalError, errors.ConnectionTimeout) — never a real driver.
# All progress -> stderr (captured by the prepare log preview). No `set -e`.

log() { echo "[build] $*" >&2; }

ROOT=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
log "repo root: $ROOT"
log "uname: $(uname -srm 2>&1)"

# --- 1. ensure a python >= 3.10 -----------------------------------------
find_py() {
  for c in python3.12 python3.11 python3.10 python3; do
    p=$(command -v "$c" 2>/dev/null) || continue
    v=$("$p" -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])' 2>/dev/null) || continue
    if [ "${v:-0}" -ge 310 ]; then echo "$p"; return 0; fi
  done
  return 1
}

PY=$(find_py || true)
if [ -z "$PY" ]; then
  log "no python>=3.10; installing via system package manager"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update >&2 2>&1
    apt-get install -y python3 python3-venv python3-pip >&2 2>&1; log "apt python3 rc=$?"
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 python3-pip >&2 2>&1; log "dnf python3 rc=$?"
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache python3 py3-pip >&2 2>&1; log "apk python3 rc=$?"
  else
    log "FATAL: no known package manager"; exit 1
  fi
  PY=$(find_py || true)
fi
[ -n "$PY" ] || { log "FATAL: python>=3.10 unavailable after install"; exit 1; }
log "python: $PY -> $("$PY" --version 2>&1)"
"$PY" -m ensurepip --upgrade >&2 2>&1; log "ensurepip rc=$?"

# --- 2. venv + PURE-PYTHON deps -----------------------------------------
VENV="$ROOT/.workers/venv"
PYEXE="$PY"
if "$PY" -m venv "$VENV" >&2 2>&1; then
  PYEXE="$VENV/bin/python3"
  "$PYEXE" -m pip install --upgrade pip >&2 2>&1; log "venv pip upgrade rc=$?"
  log "using venv python: $PYEXE"
else
  log "venv unavailable (rc=$?); using system python: $PYEXE"
fi

# DBOS's declared deps MINUS psycopg (shimmed below). All either pure-Python or
# degrade to a pure-Python path when their glibc C speedups fail to load.
log "pip install pure-python deps (no psycopg / no pgserver)"
install_deps() {
  "$1" -m pip install $2 \
    "pyyaml>=6.0.2" "python-dateutil>=2.9.0.post0" \
    "websockets>=14.0" "typer-slim>=0.17.4" "sqlalchemy>=2.0.43" >&2 2>&1
}
install_deps "$PYEXE" ""; RC=$?; log "pip install rc=$RC"
if [ "$RC" -ne 0 ]; then
  install_deps "$PYEXE" "--break-system-packages"; RC=$?; log "pip install (break-system) rc=$RC"
fi
[ "$RC" -eq 0 ] || { log "FATAL pip install failed"; exit 1; }

# --- 3. pure-Python psycopg shim ----------------------------------------
# Written into venv site-packages so `import psycopg` / `from psycopg import
# errors` succeed at musl runtime. Only the symbols DBOS references exist; any
# other attribute resolves to a synthetic Exception subclass so isinstance()
# checks in DBOS's PG-error path are simply always False on SQLite.
SITE=$("$PYEXE" -c 'import site,sys; print([p for p in site.getsitepackages() if p.endswith("site-packages")][0] if hasattr(site,"getsitepackages") else site.getusersitepackages())' 2>/dev/null)
[ -n "$SITE" ] || SITE=$("$PYEXE" -c 'import sysconfig;print(sysconfig.get_paths()["purelib"])')
log "site-packages: $SITE"
SHIM="$SITE/psycopg"
mkdir -p "$SHIM"
cat > "$SHIM/__init__.py" <<'PYSHIM'
"""Pure-Python stand-in for psycopg (SQLite-only runtime; no libpq).
DBOS imports psycopg eagerly but only uses it to classify Postgres driver
errors, a path never reached on the SQLite backend."""
class Error(Exception): pass
class Warning(Exception): pass
class InterfaceError(Error): pass
class DatabaseError(Error): pass
class OperationalError(DatabaseError): pass
class IntegrityError(DatabaseError): pass
class DataError(DatabaseError): pass
class InternalError(DatabaseError): pass
class ProgrammingError(DatabaseError): pass
class NotSupportedError(DatabaseError): pass
from . import errors  # noqa: E402,F401
__version__ = "0.0.0-wio-shim"
def __getattr__(name):
    # any other symbol -> a distinct Exception subclass (never matched by SQLite)
    return type(name, (Error,), {})
PYSHIM
cat > "$SHIM/errors.py" <<'PYSHIMERR'
"""psycopg.errors shim: every referenced error class exists as a distinct
Exception subclass; unknown names are synthesized on demand."""
class Error(Exception): pass
class OperationalError(Error): pass
class ConnectionTimeout(OperationalError): pass
class SerializationFailure(OperationalError): pass
class LockNotAvailable(OperationalError): pass
class DeadlockDetected(OperationalError): pass
class UniqueViolation(Error): pass
def __getattr__(name):
    return type(name, (Error,), {})
PYSHIMERR
log "psycopg shim installed at $SHIM"

# --- 4. runtime launcher -------------------------------------------------
cat > "$ROOT/.workers/pyrun" <<'PYRUN'
#!/bin/sh
DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
if [ -x "$DIR/venv/bin/python3" ]; then exec "$DIR/venv/bin/python3" "$@"; fi
for c in python3.12 python3.11 python3.10 python3; do
  command -v "$c" >/dev/null 2>&1 && exec "$c" "$@";
done
echo "no python3 at runtime" >&2; exit 127
PYRUN
chmod +x "$ROOT/.workers/pyrun"
log "wrote .workers/pyrun"

# --- 5. smoke ------------------------------------------------------------
cd "$ROOT"
"$PYEXE" -c 'import sys; sys.path.insert(0,".")
import sqlite3; print("[build] sqlite3", sqlite3.sqlite_version)
import psycopg; assert issubclass(psycopg.OperationalError, Exception)
from psycopg import errors; assert errors.ConnectionTimeout
import dbos
from dbos import DBOS, DBOSConfig, Queue
print("[build] dbos import OK", dbos.__file__)' >&2 2>&1
RC=$?; log "smoke rc=$RC"
[ "$RC" -eq 0 ] || { log "FATAL smoke import failed"; exit 1; }
log "done OK"
