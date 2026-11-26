#!/bin/sh
# Galaxian launcher for Linux (also works on Mac from a terminal).
# Usage: ./run_galaxian.sh [options]   e.g. ./run_galaxian.sh --scale 4
cd "$(dirname "$0")" || exit 1

PY=""
for p in python3 python; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(sys.version_info[0] < 3)' 2>/dev/null; then
        PY="$p"; break
    fi
done

if [ -z "$PY" ]; then
    echo "[galaxian] PROBLEM: Python 3 was not found."
    echo "  Debian/Ubuntu:  sudo apt install python3 python3-venv"
    echo "  Fedora:         sudo dnf install python3"
    echo "  Arch:           sudo pacman -S python"
    echo "  Mac:            https://www.python.org/downloads/"
    exit 1
fi

exec "$PY" launcher.py "$@"
