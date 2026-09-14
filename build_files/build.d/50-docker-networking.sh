#!/usr/bin/env bash
set -euo pipefail

# Docker networking is configured only by `ujust enable-docker`. Keeping this
# script as an explicit no-op preserves numeric build-script ordering while
# avoiding a Docker-specific boot-time module load on hosts that use Podman.
