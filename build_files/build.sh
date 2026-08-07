#!/usr/bin/env bash
# build.sh — runs inside the container image build
set -euo pipefail

# Thin runner. Every customization lives in build.d/, one concern per file,
# executed in filename order.
#
# The numbers are not decoration: execution order is load-bearing. The sysusers
# fixup (40-) must run after the Docker install (30-) and before libvirt daemon
# enablement (60-), so the split preserves the original top-to-bottom order of
# the single script this replaced. Insert new scripts at the number that puts
# them in the right place, don't append.
#
# `bash` rather than `source`, so a stray variable or `cd` in one script cannot
# leak into the next. Each script carries its own `set -euo pipefail`.
#
# FIREWALL_DAEMON reaches 95-firewall.sh as an inherited environment variable —
# the Containerfile passes it as a command-prefix on the RUN instruction, so it
# is in this shell's environment and every child bash inherits it.
#
# Extraction rationale, file map and hazards:
# .claude/PRPs/plans/build-sh-split.plan.md
build_d="$(dirname "${BASH_SOURCE[0]}")/build.d"

shopt -s nullglob
scripts=("${build_d}"/*.sh)
shopt -u nullglob

if [[ ${#scripts[@]} -eq 0 ]]; then
    echo "build.sh: no scripts found in ${build_d}" >&2
    exit 1
fi

for f in "${scripts[@]}"; do
    echo "=== build.d: $(basename "${f}")"
    bash "${f}"
done
