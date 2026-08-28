#!/usr/bin/env bash
set -euo pipefail

ORIGINAL_ROOT="$(cd "$(dirname "$0")" && pwd -P)"
ROOT="$ORIGINAL_ROOT"
SKIP_PULL=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --root)
      ROOT="$(cd "$2" && pwd -P)"
      shift 2
      ;;
    --skip-pull)
      SKIP_PULL=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

PORT="${REG_FACTORY_PORT:-8799}"
STATUS_URL="http://127.0.0.1:${PORT}/api/status"
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3 || command -v python || true)"
fi
[ -n "$PY" ] || { echo "Python is required" >&2; exit 1; }

status_field() {
  "$PY" - "$STATUS_URL" "$1" <<'PY'
import json
import sys
import urllib.request

try:
    with urllib.request.urlopen(sys.argv[1], timeout=5) as response:
        value = json.load(response).get(sys.argv[2], "")
        print(value)
except Exception:
    pass
PY
}

assert_no_running_tasks() {
  local running
  running="$(status_field running)"
  if [ -n "$running" ] && [ "$running" -gt 0 ] 2>/dev/null; then
    echo "The WebUI has $running running task(s). Stop them before updating." >&2
    exit 1
  fi
}

update_repository() {
  if [ -d "$ROOT/.git" ]; then
    git -C "$ROOT" pull --ff-only
    return
  fi
  command -v curl >/dev/null 2>&1 || { echo "curl is required for archive updates" >&2; exit 1; }
  local temp_root
  temp_root="$(mktemp -d)"
  trap 'rm -rf "$temp_root"' RETURN
  curl -fL "https://github.com/tiantianGPU/reg-factory/archive/refs/heads/main.tar.gz" -o "$temp_root/main.tar.gz"
  mkdir -p "$temp_root/extract"
  tar -xzf "$temp_root/main.tar.gz" --strip-components=1 -C "$temp_root/extract"
  cp -a "$temp_root/extract/." "$ROOT/"
  rm -rf "$temp_root"
  trap - RETURN
}

expected_version() {
  if [ -d "$ROOT/.git" ]; then
    git -C "$ROOT" rev-parse --short=12 HEAD
  else
    printf '%s\n' archive
  fi
}

stop_panel() {
  local panel_pid cmd cwd
  panel_pid="$(status_field pid)"
  if [ -z "$panel_pid" ]; then
    if command -v lsof >/dev/null 2>&1; then
      panel_pid="$(lsof -tiTCP:"$PORT" -sTCP:LISTEN | head -n 1 || true)"
    elif command -v fuser >/dev/null 2>&1; then
      panel_pid="$(fuser "$PORT/tcp" 2>/dev/null | awk '{print $1}' || true)"
    fi
  fi
  [ -n "$panel_pid" ] || return 0

  cmd="$(ps -p "$panel_pid" -o command= 2>/dev/null || true)"
  case "$cmd" in
    *uvicorn*webui.server:app*) ;;
    *) echo "Port $PORT is not owned by a reg-factory WebUI; refusing to stop PID $panel_pid." >&2; exit 1 ;;
  esac
  if [ -e "/proc/$panel_pid/cwd" ]; then
    cwd="$(readlink "/proc/$panel_pid/cwd" || true)"
  elif command -v lsof >/dev/null 2>&1; then
    cwd="$(lsof -a -p "$panel_pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1)"
  else
    cwd=""
  fi
  if [ -n "$cwd" ] && [ "$cwd" != "$ROOT" ]; then
    echo "WebUI PID $panel_pid has an unexpected working directory; refusing to stop it." >&2
    exit 1
  fi

  echo "Stopping old WebUI (PID $panel_pid) ..."
  kill -TERM "$panel_pid" 2>/dev/null || true
  for _ in $(seq 1 20); do
    kill -0 "$panel_pid" 2>/dev/null || return 0
    sleep 0.5
  done
  kill -KILL "$panel_pid" 2>/dev/null || true
}

wait_for_panel() {
  local expected="$1" version
  for _ in $(seq 1 45); do
    sleep 1
    version="$(status_field version)"
    if [ "$version" = "$expected" ]; then
      return 0
    fi
  done
  echo "Updated WebUI did not become healthy at $STATUS_URL" >&2
  return 1
}

echo "Updating reg-factory in $ROOT"
assert_no_running_tasks
if [ "$SKIP_PULL" -eq 0 ]; then
  update_repository
fi

assert_no_running_tasks
EXPECTED_VERSION="$(expected_version)"
stop_panel
REG_FACTORY_NONINTERACTIVE=1 bash "$ROOT/install.sh"

bash "$ROOT/start.sh" &
PANEL_JOB=$!
trap 'kill -TERM "$PANEL_JOB" 2>/dev/null || true' INT TERM
wait_for_panel "$EXPECTED_VERSION"
echo "Updated successfully: $EXPECTED_VERSION"
echo "Panel: http://127.0.0.1:$PORT/"
wait "$PANEL_JOB"
