#!/bin/sh
# FreeBSD bhyve dependency build. The preparation jail captures /usr/local
# into the dependency disk, so everything needed at runtime must live there.
set -eu

log() { echo "[build] $*" >&2; }

log "installing FreeBSD Python runtime"
pkg install -y python311 py311-sqlite3 py311-pip

VENV=/usr/local/dbos-venv
log "creating runtime environment at $VENV"
/usr/local/bin/python3.11 -m venv --system-site-packages "$VENV"

# DBOS uses SQLite in these workloads. psycopg's native/binary driver is not
# needed and is replaced below with the small import-compatible shim DBOS's
# SQLite path requires.
log "installing DBOS pure-Python dependencies"
DISABLE_SQLALCHEMY_CEXT=1 "$VENV/bin/python3" -m pip install \
  "pyyaml>=6.0.2" \
  "python-dateutil>=2.9.0.post0" \
  "websockets>=14.0" \
  "typer-slim>=0.17.4" \
  "sqlalchemy>=2.0.43"

SITE=$($VENV/bin/python3 -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')
log "copying DBOS source into $SITE"
cp -R dbos "$SITE/dbos"

SHIM="$SITE/psycopg"
mkdir -p "$SHIM"
cat > "$SHIM/__init__.py" <<'PYSHIM'
"""SQLite-only import shim for DBOS's eager psycopg imports."""
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
from . import errors
__version__ = "0.0.0-wio-shim"
def __getattr__(name):
    return type(name, (Error,), {})
PYSHIM
cat > "$SHIM/errors.py" <<'PYSHIMERR'
"""Error classes referenced by DBOS's unused PostgreSQL error path."""
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

log "running import smoke test"
PYTHONHOME= "$VENV/bin/python3" -c '
import sqlite3
import sqlalchemy
import psycopg
import dbos
from dbos import DBOS, DBOSConfig, Queue
print("sqlite", sqlite3.sqlite_version)
print("sqlalchemy", sqlalchemy.__version__)
print("dbos", dbos.__file__)
'
log "done"
