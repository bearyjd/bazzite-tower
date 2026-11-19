#!/usr/bin/env bash
# cloison/build.sh -- runs inside the Containerfile.cloison build.
set -euo pipefail

# Thin runner mirroring build_files/build.sh: every cloison/build.d/*.sh runs
# with `bash` (not `source`) in filename order, one concern per script.
build_d="$(dirname "${BASH_SOURCE[0]}")/build.d"

shopt -s nullglob
scripts=("${build_d}"/*.sh)
shopt -u nullglob

if [[ ${#scripts[@]} -eq 0 ]]; then
    echo "cloison/build.sh: no scripts found in ${build_d}" >&2
    exit 1
fi

for f in "${scripts[@]}"; do
    echo "=== cloison build.d: $(basename "${f}")"
    bash "${f}"
done
