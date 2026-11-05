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

# $1 = digest the fake registry reports for :stable, $2 = its kernel label.
# The pinned (old) digest always reports an older 44.x base with kernel 7.2.9.
stub() {
    cat > "${work}/bin/skopeo" << EOS
#!/usr/bin/env bash
echo "\$*" >> "${work}/argv"
case "\$*" in
*@sha256:${old}) echo '{"Digest":"sha256:${old}","Labels":{"org.opencontainers.image.version":"44.20261030","ostree.linux":"7.2.9-ogc3.1.fc44.x86_64"}}' ;;
*) echo '{"Digest":"sha256:$1","Labels":{"org.opencontainers.image.version":"44.20990101","ostree.linux":"${2:-}"}}' ;;
esac
EOS
    chmod +x "${work}/bin/skopeo"
}
fail() { echo "FAIL: $*" >&2; exit 1; }
run() { PATH="${work}/bin:${PATH}" GITHUB_OUTPUT="${work}/out" "${repo_root}/ci/base-bump.sh" "${work}/Containerfile"; }

# 1. upstream moved: digest rewritten, other lines untouched, outputs reported
stub "${new}" "7.2.10-ogc1.1.fc44.x86_64"
: > "${work}/out"
run
grep -q "@sha256:${new}\$" "${work}/Containerfile" || fail "digest not updated"
grep -q '^ARG OTHER=keep$' "${work}/Containerfile" || fail "unrelated line changed"
grep -q '^changed=true$' "${work}/out" || fail "changed=true missing"
grep -q 'docker://ghcr.io/ublue-os/bazzite-nvidia-open:stable' "${work}/argv" || fail "wrong repo/tag queried"
grep -q '^version=44.20990101$' "${work}/out" || fail "version missing"
grep -q "^current=sha256:${old}\$" "${work}/out" || fail "current missing"

grep -q '^automerge=true$' "${work}/out" || fail "newer kernel, same major should automerge"

# 1b. kernel downgrade / Fedora major jump / missing kernel label => automerge=false, PR still produced
for k in "7.2.8-ogc3.1.fc44.x86_64" "7.3.1-ogc1.1.fc45.x86_64" ""; do
    stub "${new}" "${k}"
    printf 'FROM scratch\nARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:%s\n' "${old}" > "${work}/Containerfile"
    : > "${work}/out"
    run
    grep -q '^changed=true$' "${work}/out" || fail "kernel '${k}': change not reported"
    grep -q '^automerge=false$' "${work}/out" || fail "kernel '${k}': should not automerge"
    grep -q '^reason=.' "${work}/out" || fail "kernel '${k}': reason missing"
done
printf 'FROM scratch\nARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:%s\nARG OTHER=keep\n' "${new}" > "${work}/Containerfile"
stub "${new}" "7.2.10-ogc1.1.fc44.x86_64"

# 1c. lookup of the CURRENT pin fails => automerge=false (fail closed), PR still produced, cause reported
cat > "${work}/bin/skopeo" << EOS
#!/usr/bin/env bash
case "\$*" in
*@sha256:${old}) exit 1 ;;
*) echo '{"Digest":"sha256:${new}","Labels":{"org.opencontainers.image.version":"44.20990101","ostree.linux":"7.2.10-ogc1.1.fc44.x86_64"}}' ;;
esac
EOS
chmod +x "${work}/bin/skopeo"
printf 'FROM scratch\nARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:%s\n' "${old}" > "${work}/Containerfile"
: > "${work}/out"
run 2> "${work}/err"
grep -q '^changed=true$' "${work}/out" || fail "current-lookup failure: change not reported"
grep -q '^automerge=false$' "${work}/out" || fail "current-lookup failure: should not automerge"
grep -q 'lookup of the CURRENT pin' "${work}/err" || fail "current-lookup failure: cause not reported"
printf 'FROM scratch\nARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:%s\nARG OTHER=keep\n' "${new}" > "${work}/Containerfile"
stub "${new}" "7.2.10-ogc1.1.fc44.x86_64"

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
