#!/bin/sh
set -eu
SIM_PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "${SIM_MANAGER_PYTHON:-python3}" "$SIM_PROJECT_DIR/activate.py" "$@"
