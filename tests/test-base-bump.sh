#!/usr/bin/env bash
# Offline test for ci/base-bump.sh: stubs skopeo and podman, so no network, registry or image pull.
set -euo pipefail
repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "${work}"' EXIT
mkdir "${work}/bin"

old=$(printf '1%.0s' {1..64})
new=$(printf 'a%.0s' {1..64})
printf 'FROM scratch\nARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:%s\nARG OTHER=keep\n' "${old}" > "${work}/Containerfile"

# $1 = digest the fake registry reports for :stable, $2 = its kernel label.
# The pinned (old) digest always reports an older 45.x base with kernel 7.2.15.
stub() {
    cat > "${work}/bin/skopeo" << EOS
#!/usr/bin/env bash
echo "\$*" >> "${work}/argv"
case "\$*" in
*@sha256:${old}) echo '{"Digest":"sha256:${old}","Labels":{"org.opencontainers.image.version":"45.20261106","ostree.linux":"7.2.15-ogc1.1.fc45.x86_64"}}' ;;
*) echo '{"Digest":"sha256:$1","Labels":{"org.opencontainers.image.version":"45.20990101","ostree.linux":"${2:-}"}}' ;;
esac
EOS
    chmod +x "${work}/bin/skopeo"
}
# podman stub: `run ... <repo>@sha256:D rpm -qa` prints ${work}/manifest-D (exit 1 if absent); pull/rmi succeed.
# Switches: ${work}/pullfail makes pull fail; ${work}/rpmfail makes `run` print then exit 1. Every call is logged.
cat > "${work}/bin/podman" << EOS
#!/usr/bin/env bash
echo "\$*" >> "${work}/podman.log"
case "\$1" in
pull) [[ ! -e "${work}/pullfail" ]] || exit 1 ;;
rmi) exit 0 ;;
run) for a in "\$@"; do case "\$a" in *@sha256:*) cat "${work}/manifest-\${a##*@sha256:}" || exit 1 ;; esac; done
    [[ ! -e "${work}/rpmfail" ]] || exit 1 ;;
esac
EOS
printf '#!/usr/bin/%s\n' 'env bash' > "${work}/bin/sleep" # no real retry back-off in tests
chmod +x "${work}/bin/sleep"
chmod +x "${work}/bin/podman"
# $1 = digest, $2... = extra "name evr" lines; a plausible base manifest (>=500 lines, has the anchors)
manifest() { local d=$1; shift; { seq -f 'pkg%g 1-1.fc44.x86_64' 1 600; printf '%s 1-1.fc44.x86_64\n' kernel-core NetworkManager bootc systemd; printf '%s\n' "$@"; } > "${work}/manifest-${d}"; }
manifest "${old}"
manifest "${new}"
fail() { echo "FAIL: $*" >&2; exit 1; }
run() { PATH="${work}/bin:${PATH}" GITHUB_OUTPUT="${work}/out" "${repo_root}/ci/base-bump.sh" "${work}/Containerfile"; }

# 1. upstream moved: digest rewritten, other lines untouched, outputs reported
stub "${new}" "7.2.16-ogc1.1.fc45.x86_64"
: > "${work}/out"
run
grep -q "@sha256:${new}\$" "${work}/Containerfile" || fail "digest not updated"
grep -q '^ARG OTHER=keep$' "${work}/Containerfile" || fail "unrelated line changed"
grep -q '^changed=true$' "${work}/out" || fail "changed=true missing"
grep -q 'docker://ghcr.io/ublue-os/bazzite-nvidia-open:stable' "${work}/argv" || fail "wrong repo/tag queried"
grep -q '^version=45.20990101$' "${work}/out" || fail "version missing"
grep -q "^current=sha256:${old}\$" "${work}/out" || fail "current missing"

grep -q '^automerge=true$' "${work}/out" || fail "newer kernel, same major, empty blast diff should automerge"
grep -q 'No blast-radius package changes' "${work}/out" || fail "empty blast report missing from outputs"
grep -q '^blast_report<<BLAST_EOF_' "${work}/out" || fail "blast_report multiline output missing"

# 1b. kernel downgrade / Fedora major jump / missing kernel label => automerge=false, PR still produced
for k in "7.2.14-ogc1.1.fc45.x86_64" "7.3.1-ogc1.1.fc46.x86_64" ""; do
    stub "${new}" "${k}"
    printf 'FROM scratch\nARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:%s\n' "${old}" > "${work}/Containerfile"
    : > "${work}/out"
    run
    grep -q '^changed=true$' "${work}/out" || fail "kernel '${k}': change not reported"
    grep -q '^automerge=false$' "${work}/out" || fail "kernel '${k}': should not automerge"
    grep -q '^reason=.' "${work}/out" || fail "kernel '${k}': reason missing"
done
printf 'FROM scratch\nARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:%s\nARG OTHER=keep\n' "${new}" > "${work}/Containerfile"
stub "${new}" "7.2.16-ogc1.1.fc45.x86_64"

# 1c. lookup of the CURRENT pin fails => automerge=false (fail closed), PR still produced, cause reported
cat > "${work}/bin/skopeo" << EOS
#!/usr/bin/env bash
case "\$*" in
*@sha256:${old}) exit 1 ;;
*) echo '{"Digest":"sha256:${new}","Labels":{"org.opencontainers.image.version":"45.20990101","ostree.linux":"7.2.16-ogc1.1.fc45.x86_64"}}' ;;
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
stub "${new}" "7.2.16-ogc1.1.fc45.x86_64"

# 1d. non-empty blast-radius diff => automerge=false, names in reason and full diff in blast_report, sanitized
# shellcheck disable=SC2016 # literal shell metacharacters on purpose
manifest "${new}" 'qemu-kvm 9.1-1.fc44.x86_64' 'qemu-evil@x[y]|z$(id) 1-1.fc44.x86_64'
manifest "${old}" 'qemu-kvm 9.0-1.fc44.x86_64'
printf 'FROM scratch\nARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:%s\n' "${old}" > "${work}/Containerfile"
: > "${work}/out"
run
grep -q '^changed=true$' "${work}/out" || fail "blast diff: change not reported"
grep -q '^automerge=false$' "${work}/out" || fail "blast diff: should not automerge"
grep -q '^reason=.*qemu-kvm' "${work}/out" || fail "blast diff: reason must name qemu-kvm"
grep -q 'qemu-kvm. 9.0-1.fc44.x86_64 -> 9.1-1.fc44.x86_64' "${work}/out" || fail "blast diff: full report missing"
if grep -q '[@|$]' "${work}/out"; then fail "blast diff: unsanitized char reached GITHUB_OUTPUT"; fi

# 1e. diff cannot be computed (pull/run of the CURRENT pin fails; both manifests implausible) => automerge=false
manifest "${new}"
rm -f "${work}/manifest-${old}"
printf 'FROM scratch\nARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:%s\n' "${old}" > "${work}/Containerfile"
: > "${work}/out"
run 2> "${work}/err"
grep -q '^changed=true$' "${work}/out" || fail "blast failure: change not reported"
grep -q '^automerge=false$' "${work}/out" || fail "blast failure: should not automerge"
grep -q '^reason=.*not computed' "${work}/out" || fail "blast failure: reason should say not computed"
grep -q 'could not be computed' "${work}/out" || fail "blast failure: PR body text missing"
for d in "${old}" "${new}"; do echo 'systemd 1-1' > "${work}/manifest-${d}"; done
printf 'FROM scratch\nARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:%s\n' "${old}" > "${work}/Containerfile"
: > "${work}/out"
run 2> /dev/null
grep -q '^automerge=false$' "${work}/out" || fail "identical truncated manifests must not read as an empty diff"
manifest "${old}"
manifest "${new}"
printf 'FROM scratch\nARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:%s\nARG OTHER=keep\n' "${new}" > "${work}/Containerfile"

# 1f. new guards: each must force automerge=false with the PR body fallback text
blocked() { # $1 = label; expects changed=true, automerge=false, not-computed reason
    printf 'FROM scratch\nARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:%s\n' "${old}" > "${work}/Containerfile"
    : > "${work}/out"
    run 2> /dev/null
    grep -q '^changed=true$' "${work}/out" || fail "$1: change not reported"
    grep -q '^automerge=false$' "${work}/out" || fail "$1: should not automerge"
    grep -q 'could not be computed' "${work}/out" || fail "$1: PR body fallback text missing"
}
manifest "${old}"
manifest "${new}"
# pull failure (all retries)
touch "${work}/pullfail"
blocked "pull failure"
rm -f "${work}/pullfail"
# rpm exits non-zero after printing a plausible manifest
touch "${work}/rpmfail"
blocked "rpm nonzero with output"
rm -f "${work}/rpmfail"
# >=500 lines but a missing anchor, in either manifest
for anchor in kernel-core NetworkManager bootc systemd; do
    for d in "${old}" "${new}"; do
        manifest "${old}"
        manifest "${new}"
        grep -v "^${anchor} " "${work}/manifest-${d}" > "${work}/m.tmp"
        mv "${work}/m.tmp" "${work}/manifest-${d}"
        blocked "manifest missing ${anchor}"
    done
done
# 60 lines with all anchors is still too small
for d in "${old}" "${new}"; do printf '%s 1-1\n' kernel-core NetworkManager bootc systemd > "${work}/manifest-${d}"; seq -f 'p%g 1-1' 1 60 >> "${work}/manifest-${d}"; done
blocked "short manifest"
manifest "${old}"
manifest "${new}"
# offline, no implicit pull on the rpm step
grep -q '^run .*--network=none' "${work}/podman.log" || fail "podman run must use --network=none"
grep -q '^run .*--pull=never' "${work}/podman.log" || fail "podman run must use --pull=never"
# both refs are removed on exit
grep -q "^rmi -f .*@sha256:${old} .*@sha256:${new}" "${work}/podman.log" || fail "EXIT trap must rmi both refs"

# 1g. over-cap diff (400 changed qemu pkgs): blocked, report capped at 300 lines
manifest "${old}"
manifest "${new}"
for i in $(seq 1 400); do echo "qemu-p${i} 2-1.fc44.x86_64" >> "${work}/manifest-${new}"; done
printf 'FROM scratch\nARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:%s\n' "${old}" > "${work}/Containerfile"
: > "${work}/out"
run
grep -q '^automerge=false$' "${work}/out" || fail "over-cap: should not automerge"
[[ $(grep -c '^- `qemu-p' "${work}/out") -le 300 ]] || fail "over-cap: report not capped"

# 1h. report containing the multiline delimiter => fail closed, report dropped
cat > "${work}/bin/openssl" << 'EOS'
#!/usr/bin/env bash
echo deadbeefdeadbeefdeadbeefdeadbeef
EOS
chmod +x "${work}/bin/openssl"
manifest "${old}"
manifest "${new}" 'qemu-BLAST_EOF_deadbeefdeadbeefdeadbeefdeadbeef 1-1.fc44.x86_64'
printf 'FROM scratch\nARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:%s\n' "${old}" > "${work}/Containerfile"
: > "${work}/out"
run 2> /dev/null
grep -q '^automerge=false$' "${work}/out" || fail "delimiter collision: should not automerge"
grep -q '^reason=.*delimiter collision' "${work}/out" || fail "delimiter collision: reason missing"
grep -q 'could not be computed' "${work}/out" || fail "delimiter collision: fallback text missing"
[[ $(grep -c '^BLAST_EOF_deadbeef' "${work}/out") -eq 1 ]] || fail "delimiter collision: delimiter appears more than once"
rm -f "${work}/bin/openssl"
manifest "${new}"
printf 'FROM scratch\nARG BASE_IMAGE=ghcr.io/ublue-os/bazzite-nvidia-open@sha256:%s\nARG OTHER=keep\n' "${new}" > "${work}/Containerfile"

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
