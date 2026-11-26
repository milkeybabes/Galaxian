#!/bin/sh
# Galaxian launcher for Mac - double-click in Finder to play.
# (First time only, if macOS blocks it: right-click > Open > Open.)
cd "$(dirname "$0")" || exit 1

PY=""
for p in python3 /usr/local/bin/python3 /opt/homebrew/bin/python3 python; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info[0] < 3)' 2>/dev/null; then
        PY="$p"; break
    fi
done

if [ -z "$PY" ]; then
    echo "[galaxian] PROBLEM: Python 3 was not found."
    echo "  Install it from https://www.python.org/downloads/ (or: brew install python),"
    echo "  then double-click this file again."
    echo
    read -r -p "Press Return to close..." _
    exit 1
fi

"$PY" launcher.py "$@"
status=$?
if [ $status -ne 0 ]; then
    echo
    read -r -p "Press Return to close..." _
fi
exit $status
