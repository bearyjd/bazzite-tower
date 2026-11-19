#!/usr/bin/env bash
# The live-ISO recipe must build titanoboa from a pinned commit SHA, never from
# the moving default branch.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
justfile="${repo_root}/Justfile"

if grep -Eq 'git clone .*ublue-os/titanoboa' "${justfile}"; then
    echo "FAIL: Justfile clones titanoboa without pinning a revision" >&2
    exit 1
fi
if ! grep -Eq '^titanoboa_rev := "[0-9a-f]{40}"$' "${justfile}"; then
    echo "FAIL: Justfile must define titanoboa_rev as a 40-char commit SHA" >&2
    exit 1
fi
if ! grep -Eq 'fetch --depth 1 https://github.com/ublue-os/titanoboa "\{\{ ?titanoboa_rev ?\}\}"' "${justfile}"; then
    echo "FAIL: build-iso-live does not check out titanoboa_rev" >&2
    exit 1
fi
echo "OK: titanoboa checkout is pinned"
