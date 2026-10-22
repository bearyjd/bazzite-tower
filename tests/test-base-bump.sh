#!/usr/bin/env bash
# Offline test for ci/base-bump.sh: stubs skopeo, so no network or registry.
set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "${work}"' EXIT
mkdir "${work}/bin"

old=$(printf '1%.0s' {1..64})
new=$(printf 'a%.0s' {1..64})
printf 'FROM scratch\nARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:%s\nARG OTHER=keep\n' "${old}" > "${work}/Containerfile"

stub() { # $1 = digest the fake registry reports
    printf '#!/usr/bin/env bash\necho "$*" > "%s/argv"\necho '"'"'{"Digest":"sha256:%s","Labels":{"org.opencontainers.image.version":"44.20990101"}}'"'"'\n' "${work}" "$1" > "${work}/bin/skopeo"
    chmod +x "${work}/bin/skopeo"
}
fail() { echo "FAIL: $*" >&2; exit 1; }
run() { PATH="${work}/bin:${PATH}" GITHUB_OUTPUT="${work}/out" "${repo_root}/ci/base-bump.sh" "${work}/Containerfile"; }

# 1. upstream moved: digest rewritten, other lines untouched, outputs reported
stub "${new}"
: > "${work}/out"
run
grep -q "@sha256:${new}\$" "${work}/Containerfile" || fail "digest not updated"
grep -q '^ARG OTHER=keep$' "${work}/Containerfile" || fail "unrelated line changed"
grep -q '^changed=true$' "${work}/out" || fail "changed=true missing"
grep -q 'docker://ghcr.io/ublue-os/bazzite-nvidia-open:stable' "${work}/argv" || fail "wrong repo/tag queried"
grep -q '^version=44.20990101$' "${work}/out" || fail "version missing"
grep -q "^current=sha256:${old}\$" "${work}/out" || fail "current missing"

# 2. already current: idempotent, no change reported
: > "${work}/out"
run
grep -q '^changed=false$' "${work}/out" || fail "second run should report changed=false"

# 3. malformed digest from upstream is refused and leaves the file alone
stub "notadigest"
cp "${work}/Containerfile" "${work}/before"
if run 2> /dev/null; then fail "malformed digest accepted"; fi
cmp -s "${work}/Containerfile" "${work}/before" || fail "file modified on bad digest"

# 4. a Containerfile with no digest pin is refused
printf 'ARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open:stable\n' > "${work}/Containerfile"
if run 2> /dev/null; then fail "unpinned Containerfile accepted"; fi

echo "PASS: base-bump"
