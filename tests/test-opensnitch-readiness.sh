#!/usr/bin/env bash
# Behavioral legacy/default dispatch checks; all commands/files are fixtures.
set -euo pipefail
repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
bash -n "${repo_root}/system_files/usr/libexec/bazzite-tower-opensnitch-readiness"
PYTHONDONTWRITEBYTECODE=1 python3 "${repo_root}/tests/test-opensnitch-readiness.py"
