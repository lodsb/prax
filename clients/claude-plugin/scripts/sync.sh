#!/bin/sh
# The session-end hook's launcher: find a Python and run sync.py with it.
#
# A bare `python` in the hook's command was the Microsoft Store's stub on
# a Windows machine (WindowsApps\python, "Python was not found", exit 49),
# so sync.py never started and its own hand-over to PRAX_PYTHON was never
# reached; the session ended without a word (the tt01 session,
# 2026-11-13). Tried in order: PRAX_PYTHON, the Windows launcher `py -3`,
# python3, python; a stub under WindowsApps is not a Python. None found
# is a line on stderr, never a failed session.

here=$(dirname "$0")
run() {
    exec "$@" "$here/sync.py"
}

if [ -n "$PRAX_PYTHON" ]; then
    if [ -f "$PRAX_PYTHON" ]; then
        run "$PRAX_PYTHON"
    fi
    echo "prax sync: PRAX_PYTHON names no file ($PRAX_PYTHON); trying others" >&2
fi
for name in py python3 python; do
    found=$(command -v "$name" 2>/dev/null) || continue
    case "$found" in
        *WindowsApps*) continue ;;  # the Store's stub, not a Python
    esac
    if [ "$name" = py ]; then
        run "$found" -3
    fi
    run "$found"
done
echo "prax sync: no Python found for the session-end sync; set PRAX_PYTHON" \
    "to the one prax is installed in" >&2
exit 0
