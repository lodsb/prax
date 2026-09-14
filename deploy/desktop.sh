#!/usr/bin/env bash
# The desktop as prax's server, on Linux and macOS: the door, llama-server
# and the worker as services that start when you log in and come back
# after a crash, a nightly backlog pass, a nightly backup — systemd user
# units on Linux, launchd agents on macOS, under your own account, nothing
# system-wide. The twin of deploy/desktop.ps1 (docs/howto.md 4b).
#
#   deploy/desktop.sh install --data-dir ~/prax-data --backup /Volumes/backup/prax \
#       --llama-model ~/models/Qwen3.6-35B-A3B-UD-Q4_K_S.gguf \
#       --llama-args "--mmproj mmproj-F16.gguf --slots 2 --cpu-moe 2 --ubatch 256 --image-max-tokens 1024 --no-thinking"
#   deploy/desktop.sh start            # now; a login starts them anyway
#   deploy/desktop.sh status
#   deploy/desktop.sh stop worker      # one, or all
#   deploy/desktop.sh uninstall        # the services; the store is untouched
#
# What gets installed (--llama-model and --backup are optional; without
# them those two are left out):
#
#   llama-server   at login   scripts/llama_server.sh --model … + --llama-args
#   door           at login   prax serve --host <bind> --port <port>
#   worker         at login   prax work --watch, after the door answers
#   nightly        03:00      prax work --scope all --limit <n>   (--nightly-at)
#   backup         04:30      prax backup <dir> [--no-archive]     (--backup-at)
#
# Each service runs this script again with `run <name>`, which sets the
# environment, rotates the logs (<data dir>/logs/<name>.log and .err.log,
# ten kept; <name>.runs.log lists every start) and execs the process, so
# the service manager watches the process itself and restarts it when it
# dies. Secrets never go into a unit: the token comes from PRAX_TOKEN in
# the environment, from <data dir>/door.token (one line), or from <data
# dir>/desktop.env (KEY=value lines, read by every `run`; ANTHROPIC_API_KEY
# goes there too, since a service has no shell profile); without a token
# the door answers this machine only.
#
# Linux: user units stop at logout and start at login; `loginctl
# enable-linger $USER` keeps them running without a session (the script
# says so). macOS: agents in ~/Library/LaunchAgents load at login;
# `stop` unloads until the next login or `start`.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
SELF="$HERE/deploy/desktop.sh"
PRAX="$HERE/.venv/bin/prax"
NAMES="llama-server door worker nightly backup"
LABEL="io.github.lodsb.prax"

DATA_DIR="${PRAX_DATA_DIR:-$HERE/data}"
BIND="0.0.0.0"; PORT=8000
LLAMA_MODEL=""; LLAMA_ARGS=""
BACKUP=""; BACKUP_ARCHIVE=0
NIGHTLY_LIMIT=100; NIGHTLY_STEPS="parse,titles,extract,embed"; NIGHTLY_AT="03:00"; BACKUP_AT="04:30"
INTERVAL=20

cmd="${1:-status}"; shift || true
ONLY=""
if [ $# -gt 0 ] && [[ "$1" != --* ]]; then ONLY="$1"; shift; fi
while [ $# -gt 0 ]; do
  case "$1" in
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    --bind) BIND="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --llama-model) LLAMA_MODEL="$2"; shift 2 ;;
    --llama-args) LLAMA_ARGS="$2"; shift 2 ;;
    --backup) BACKUP="$2"; shift 2 ;;
    --backup-archive) BACKUP_ARCHIVE=1; shift ;;
    --nightly-limit) NIGHTLY_LIMIT="$2"; shift 2 ;;
    --nightly-steps) NIGHTLY_STEPS="$2"; shift 2 ;;
    --nightly-at) NIGHTLY_AT="$2"; shift 2 ;;
    --backup-at) BACKUP_AT="$2"; shift 2 ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    -h|--help) sed -n '2,38p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
DATA_DIR="$(cd "$(dirname "$DATA_DIR")" 2>/dev/null && pwd)/$(basename "$DATA_DIR")"

case "$(uname -s)" in
  Darwin) OS=mac ;;
  Linux) OS=linux ;;
  *) echo "this is for Linux (systemd --user) and macOS (launchd); on Windows: deploy\\desktop.ps1" >&2; exit 2 ;;
esac

chosen() { if [ -n "$ONLY" ]; then echo "$ONLY"; else echo "$NAMES"; fi; }

# ------------------------------------------------------------ environment

load_environment() {
  export PRAX_DATA_DIR="$DATA_DIR"
  if [ -f "$DATA_DIR/desktop.env" ]; then
    set -a; . "$DATA_DIR/desktop.env"; set +a
  fi
  if [ -z "${PRAX_TOKEN:-}" ] && [ -f "$DATA_DIR/door.token" ]; then
    PRAX_TOKEN="$(head -n 1 "$DATA_DIR/door.token" | tr -d '[:space:]')"
    export PRAX_TOKEN
  fi
  export PRAX_DOOR="http://127.0.0.1:$PORT"
  export PYTHONUNBUFFERED=1
}

rotate_logs() {  # <name>: rotates <name>.log and <name>.err.log, ten kept
  local name="$1" logs="$DATA_DIR/logs" f stamp
  mkdir -p "$logs"
  for suffix in log err.log; do
    f="$logs/$name.$suffix"
    if [ -s "$f" ]; then
      stamp="$(date -r "$f" +%Y%m%d-%H%M%S 2>/dev/null || date +%Y%m%d-%H%M%S)"
      mv -f "$f" "$logs/$name.$stamp.$suffix"
    fi
  done
  ls -t "$logs/$name".20*.log "$logs/$name".20*.err.log 2>/dev/null | tail -n +11 | xargs rm -f 2>/dev/null || true
}

wait_for_door() {  # any HTTP answer counts, a 401 included
  local i
  for i in $(seq 1 30); do
    if curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$PORT/changes" 2>/dev/null | grep -q '^[0-9]'; then return 0; fi
    sleep 2
  done
  return 1
}

run_service() {  # exec the process with its output in the logs
  local name="$1"; shift
  local logs="$DATA_DIR/logs"
  load_environment
  rotate_logs "$name"
  echo "$(date +%FT%T) start: $*" >> "$logs/$name.runs.log"
  if [ "$name" = worker ]; then wait_for_door || echo "$(date +%FT%T) the door did not answer in 60 s; starting anyway" >> "$logs/$name.runs.log"; fi
  exec "$@" >> "$logs/$name.log" 2>> "$logs/$name.err.log"
}

if [ "$cmd" = run ]; then
  [ -n "$ONLY" ] || { echo "run <name>" >&2; exit 2; }
  case "$ONLY" in
    door) run_service door "$PRAX" serve --host "$BIND" --port "$PORT" ;;
    worker) run_service worker "$PRAX" work --watch --interval "$INTERVAL" ;;
    nightly) run_service nightly "$PRAX" work --scope all --limit "$NIGHTLY_LIMIT" --steps "$NIGHTLY_STEPS" ;;
    backup)
      [ -n "$BACKUP" ] || { echo "no backup directory: install with --backup" >&2; exit 2; }
      if [ "$BACKUP_ARCHIVE" = 1 ]; then run_service backup "$PRAX" backup "$BACKUP"
      else run_service backup "$PRAX" backup "$BACKUP" --no-archive; fi ;;
    llama-server)
      [ -n "$LLAMA_MODEL" ] || { echo "no model: install with --llama-model" >&2; exit 2; }
      # shellcheck disable=SC2086
      run_service llama-server bash "$HERE/scripts/llama_server.sh" --model "$LLAMA_MODEL" $LLAMA_ARGS ;;
    *) echo "unknown service: $ONLY" >&2; exit 2 ;;
  esac
fi

# --------------------------------------------------------- what to install

# the `run` line of each service, as one string of shell words; double
# quotes, which systemd's ExecStart and a shell read the same way
q() { printf '"%s"' "$(printf %s "$1" | sed 's/[\\"]/\\&/g')"; }
run_line() {
  local common="bash $(q "$SELF") run $1 --data-dir $(q "$DATA_DIR") --port $PORT"
  case "$1" in
    door) echo "$common --bind $BIND" ;;
    worker) echo "$common --interval $INTERVAL" ;;
    nightly) echo "$common --nightly-limit $NIGHTLY_LIMIT --nightly-steps $(q "$NIGHTLY_STEPS")" ;;
    backup) if [ "$BACKUP_ARCHIVE" = 1 ]; then echo "$common --backup $(q "$BACKUP") --backup-archive"; else echo "$common --backup $(q "$BACKUP")"; fi ;;
    llama-server) echo "$common --llama-model $(q "$LLAMA_MODEL") --llama-args $(q "$LLAMA_ARGS")" ;;
  esac
}

wanted() {  # the services this install has
  local out="door worker nightly"
  [ -n "$LLAMA_MODEL" ] && out="llama-server $out"
  [ -n "$BACKUP" ] && out="$out backup"
  echo "$out"
}

is_daily() { [ "$1" = nightly ] || [ "$1" = backup ]; }
hour_of() { if [ "$1" = nightly ]; then echo "${NIGHTLY_AT%%:*}"; else echo "${BACKUP_AT%%:*}"; fi; }
minute_of() { if [ "$1" = nightly ]; then echo "${NIGHTLY_AT##*:}"; else echo "${BACKUP_AT##*:}"; fi; }

# ------------------------------------------------------------------ linux

UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

linux_install() {
  mkdir -p "$UNITS"
  local name after=""
  for name in $(wanted); do
    if is_daily "$name"; then
      cat > "$UNITS/prax-$name.service" <<EOF
[Unit]
Description=prax $name (deploy/desktop.sh)
[Service]
Type=oneshot
WorkingDirectory=$HERE
ExecStart=$(run_line "$name")
EOF
      cat > "$UNITS/prax-$name.timer" <<EOF
[Unit]
Description=prax $name, daily at $(hour_of "$name"):$(minute_of "$name")
[Timer]
OnCalendar=*-*-* $(hour_of "$name"):$(minute_of "$name"):00
Persistent=true
[Install]
WantedBy=timers.target
EOF
    else
      after=""
      [ "$name" = worker ] && after="After=prax-door.service
Wants=prax-door.service"
      cat > "$UNITS/prax-$name.service" <<EOF
[Unit]
Description=prax $name (deploy/desktop.sh)
$after
[Service]
Type=simple
WorkingDirectory=$HERE
ExecStart=$(run_line "$name")
Restart=on-failure
RestartSec=60
[Install]
WantedBy=default.target
EOF
    fi
    echo "written     $UNITS/prax-$name.service"
  done
  for name in $NAMES; do
    case " $(wanted) " in *" $name "*) ;; *)
      if [ -f "$UNITS/prax-$name.service" ]; then
        systemctl --user disable --now "prax-$name.service" "prax-$name.timer" 2>/dev/null || true
        rm -f "$UNITS/prax-$name.service" "$UNITS/prax-$name.timer"; echo "removed     prax-$name (not in this install)"
      fi ;;
    esac
  done
  systemctl --user daemon-reload
  for name in $(wanted); do
    if is_daily "$name"; then systemctl --user enable --now "prax-$name.timer" >/dev/null 2>&1
    else systemctl --user enable "prax-$name.service" >/dev/null 2>&1; fi
  done
  echo
  echo "Not started: deploy/desktop.sh start starts them now; a login starts them anyway."
  local me="${USER:-$(id -un)}"
  if ! loginctl show-user "$me" 2>/dev/null | grep -q 'Linger=yes'; then
    echo "To keep them running without a login session:  loginctl enable-linger $me"
  fi
}

linux_start() { local n; for n in $(chosen); do is_daily "$n" && continue; [ -f "$UNITS/prax-$n.service" ] || { echo "no unit prax-$n (install first)"; continue; }; systemctl --user start "prax-$n.service" && echo "started  prax-$n"; done; }
linux_stop() { local n; for n in $(chosen); do [ -f "$UNITS/prax-$n.service" ] || continue; systemctl --user stop "prax-$n.service" 2>/dev/null && echo "stopped  prax-$n"; done; }
linux_uninstall() {
  local n
  for n in $(chosen); do
    [ -f "$UNITS/prax-$n.service" ] || continue
    systemctl --user disable --now "prax-$n.service" 2>/dev/null || true
    [ -f "$UNITS/prax-$n.timer" ] && systemctl --user disable --now "prax-$n.timer" 2>/dev/null || true
    rm -f "$UNITS/prax-$n.service" "$UNITS/prax-$n.timer"; echo "removed  prax-$n"
  done
  systemctl --user daemon-reload
}
linux_status() {
  echo "services (systemd --user):"
  local n state next
  for n in $NAMES; do
    if [ ! -f "$UNITS/prax-$n.service" ]; then printf '  %-14s not installed\n' "$n"; continue; fi
    if is_daily "$n"; then
      next="$(systemctl --user show "prax-$n.timer" -p NextElapseUSecRealtime --value 2>/dev/null || true)"
      printf '  %-14s %-9s next %s\n' "$n" "$(systemctl --user is-active "prax-$n.timer" 2>/dev/null || true)" "${next:-?}"
    else
      state="$(systemctl --user is-active "prax-$n.service" 2>/dev/null || true)"
      printf '  %-14s %s\n' "$n" "$state"
    fi
  done
}

# -------------------------------------------------------------------- mac

AGENTS="$HOME/Library/LaunchAgents"
plist_of() { echo "$AGENTS/$LABEL.$1.plist"; }
domain() { echo "gui/$(id -u)"; }

plist_args() {  # the ProgramArguments of a service, one <string> per word
  local w
  # shellcheck disable=SC2046
  eval "set -- $(run_line "$1")"
  for w in "$@"; do printf '      <string>%s</string>\n' "$(printf %s "$w" | sed 's/&/\&amp;/g; s/</\&lt;/g')"; done
}

mac_install() {
  mkdir -p "$AGENTS"
  local name schedule keepalive
  for name in $(wanted); do
    if is_daily "$name"; then
      schedule="    <key>StartCalendarInterval</key>
    <dict><key>Hour</key><integer>$((10#$(hour_of "$name")))</integer><key>Minute</key><integer>$((10#$(minute_of "$name")))</integer></dict>
    <key>RunAtLoad</key><false/>"
      keepalive=""
    else
      schedule="    <key>RunAtLoad</key><true/>"
      keepalive="    <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
    <key>ThrottleInterval</key><integer>60</integer>"
    fi
    cat > "$(plist_of "$name")" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL.$name</string>
    <key>ProgramArguments</key>
    <array>
$(plist_args "$name")    </array>
    <key>WorkingDirectory</key><string>$HERE</string>
$schedule
$keepalive
</dict>
</plist>
EOF
    echo "written     $(plist_of "$name")"
  done
  for name in $NAMES; do
    case " $(wanted) " in *" $name "*) ;; *)
      if [ -f "$(plist_of "$name")" ]; then
        launchctl bootout "$(domain)/$LABEL.$name" 2>/dev/null || true
        rm -f "$(plist_of "$name")"; echo "removed     $LABEL.$name (not in this install)"
      fi ;;
    esac
  done
  # the daily ones are loaded now (nothing runs before their hour); the
  # services wait for `start` or the next login
  for name in $(wanted); do
    if is_daily "$name"; then launchctl bootout "$(domain)/$LABEL.$name" 2>/dev/null || true; launchctl bootstrap "$(domain)" "$(plist_of "$name")"; fi
  done
  echo
  echo "Not started: deploy/desktop.sh start starts them now; a login starts them anyway."
}

mac_start() { local n; for n in $(chosen); do is_daily "$n" && continue; [ -f "$(plist_of "$n")" ] || { echo "no agent $LABEL.$n (install first)"; continue; }; launchctl bootout "$(domain)/$LABEL.$n" 2>/dev/null || true; launchctl bootstrap "$(domain)" "$(plist_of "$n")" && echo "started  $LABEL.$n"; done; }
mac_stop() { local n; for n in $(chosen); do launchctl bootout "$(domain)/$LABEL.$n" 2>/dev/null && echo "stopped  $LABEL.$n"; done; }
mac_uninstall() { local n; for n in $(chosen); do [ -f "$(plist_of "$n")" ] || continue; launchctl bootout "$(domain)/$LABEL.$n" 2>/dev/null || true; rm -f "$(plist_of "$n")"; echo "removed  $LABEL.$n"; done; }
mac_status() {
  echo "agents (launchd, $AGENTS):"
  local n line
  for n in $NAMES; do
    if [ ! -f "$(plist_of "$n")" ]; then printf '  %-14s not installed\n' "$n"; continue; fi
    line="$(launchctl list 2>/dev/null | awk -v l="$LABEL.$n" '$3 == l { print ($1 == "-" ? "loaded, not running (last exit " $2 ")" : "running, pid " $1) }')"
    printf '  %-14s %s\n' "$n" "${line:-not loaded}"
  done
}

# ------------------------------------------------------------ dispatch

common_status() {
  load_environment
  echo "processes:"
  pgrep -fl 'llama-server|prax (serve|work)' 2>/dev/null | sed 's/^/  /' || echo "  none"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 ${PRAX_TOKEN:+-H "Authorization: Bearer $PRAX_TOKEN"} "http://127.0.0.1:$PORT/changes" 2>/dev/null || true)"
  case "$code" in
    200) echo "door:  answering on :$PORT" ;;
    401|403) echo "door:  answering on :$PORT, but not to this token" ;;
    *) echo "door:  not answering on :$PORT" ;;
  esac
  if [ -n "${PRAX_TOKEN:-}" ]; then echo "token: set"; else echo "token: none (the door answers this machine only)"; fi
  echo "logs:  $DATA_DIR/logs"
}

case "$cmd" in
  install)
    [ -x "$PRAX" ] || { echo "no prax in $HERE/.venv (docs/howto.md 1)" >&2; exit 2; }
    [ -d "$DATA_DIR" ] || { echo "no data directory at $DATA_DIR" >&2; exit 2; }
    [ -z "$LLAMA_MODEL" ] || [ -f "$LLAMA_MODEL" ] || { echo "model not found: $LLAMA_MODEL" >&2; exit 2; }
    [ -z "$BACKUP" ] || [[ "$BACKUP" = /* ]] || { echo "--backup must be an absolute path" >&2; exit 2; }
    "${OS}_install"
    if [ -z "${PRAX_TOKEN:-}" ] && [ ! -f "$DATA_DIR/door.token" ] && ! grep -qs '^PRAX_TOKEN=' "$DATA_DIR/desktop.env"; then
      echo "No token yet: the door will answer this machine only. Write one line to"
      echo "$DATA_DIR/door.token (or PRAX_TOKEN= in $DATA_DIR/desktop.env), then restart the door."
    fi ;;
  start) "${OS}_start" ;;
  stop) "${OS}_stop" ;;
  uninstall) "${OS}_uninstall" ;;
  status) "${OS}_status"; common_status ;;
  *) echo "usage: deploy/desktop.sh install|start|stop|status|uninstall [name] [options]  (deploy/desktop.sh --help)" >&2; exit 2 ;;
esac
