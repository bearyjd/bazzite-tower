#!/usr/bin/env bash
# M1 placeholder: proves the variant build path runs. Installs nothing.
set -euo pipefail

mkdir -p /usr/share/cloison
printf 'cloison-m1-scaffold\n' > /usr/share/cloison/variant
