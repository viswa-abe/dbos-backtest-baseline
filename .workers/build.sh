#!/bin/sh
# POSIX sh (prepare runs this with /bin/sh, as root). Provisioning build.
# Base image reality: Linux x86_64 (Amazon Linux 2023), root, NO python3.
# So we install python3.11 (DBOS requires >=3.10; AL2023 default is 3.9), then
# a venv with DBOS runtime deps + embedded postgres (pgserver, PG16) + a no-op
# uuid-ossp shim. DBOS itself is imported from the repo tree (the SUT).
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
  if command -v dnf >/dev/null 2>&1; then
    dnf install -y python3.11 python3.11-pip >&2 2>&1; log "dnf python3.11 rc=$?"
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3.11 python3.11-pip >&2 2>&1; log "yum python3.11 rc=$?"
  elif command -v apt-get >/dev/null 2>&1; then
    apt-get update >&2 2>&1
    # Ubuntu Noble ships python3.12 as `python3` (>=3.10). Use generic names.
    apt-get install -y python3 python3-venv python3-pip >&2 2>&1; log "apt python3 rc=$?"
  elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache python3 py3-pip >&2 2>&1; log "apk python3 rc=$?"
  else
    log "FATAL: no known package manager"; exit 1
  fi
  PY=$(find_py || true)
fi
if [ -z "$PY" ]; then
  log "FATAL: python>=3.10 still unavailable after install"; exit 1
fi
log "python: $PY -> $("$PY" --version 2>&1)"
"$PY" -m ensurepip --upgrade >&2 2>&1; log "ensurepip rc=$?"

# --- 2. venv + deps ------------------------------------------------------
VENV="$ROOT/.workers/venv"
PYEXE="$PY"
if "$PY" -m venv "$VENV" >&2 2>&1; then
  PYEXE="$VENV/bin/python3"
  "$PYEXE" -m pip install --upgrade pip >&2 2>&1; log "venv pip upgrade rc=$?"
  log "using venv python: $PYEXE"
else
  log "venv unavailable (rc=$?); using system python: $PYEXE"
fi

log "pip install deps + pgserver (needs build-time network)"
"$PYEXE" -m pip install \
  "pyyaml>=6.0.2" "python-dateutil>=2.9.0.post0" "psycopg[binary]>=3.1" \
  "websockets>=14.0" "typer-slim>=0.17.4" "sqlalchemy>=2.0.43" "pgserver" >&2 2>&1
RC=$?; log "pip install rc=$RC"
if [ "$RC" -ne 0 ]; then
  "$PYEXE" -m pip install --break-system-packages \
    "pyyaml>=6.0.2" "python-dateutil>=2.9.0.post0" "psycopg[binary]>=3.1" \
    "websockets>=14.0" "typer-slim>=0.17.4" "sqlalchemy>=2.0.43" "pgserver" >&2 2>&1
  RC=$?; log "pip install (break-system) rc=$RC"
fi
[ "$RC" -eq 0 ] || { log "FATAL pip install failed"; exit 1; }

# --- 3. uuid-ossp shim ---------------------------------------------------
EXTDIR=$("$PYEXE" -c 'import os,pgserver;print(os.path.join(os.path.dirname(pgserver.__file__),"pginstall","share","postgresql","extension"))' 2>>/dev/stderr)
log "pg extension dir: $EXTDIR"
if [ -n "$EXTDIR" ] && [ ! -f "$EXTDIR/uuid-ossp.control" ]; then
  printf "comment = 'no-op shim'\ndefault_version = '1.1'\nrelocatable = true\n" > "$EXTDIR/uuid-ossp.control"
  printf -- "-- no-op shim; DBOS uses built-in gen_random_uuid()\n" > "$EXTDIR/uuid-ossp--1.1.sql"
  log "uuid-ossp shim installed"
fi

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
import pgserver, dbos
from dbos import DBOS, DBOSConfig, Queue
print("[build] dbos+pgserver import OK", dbos.__file__)' >&2 2>&1
RC=$?; log "smoke rc=$RC"
[ "$RC" -eq 0 ] || { log "FATAL smoke import failed"; exit 1; }
log "done OK"
