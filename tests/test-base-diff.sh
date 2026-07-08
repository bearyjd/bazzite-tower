#!/usr/bin/env bash
# tests/test-base-diff.sh — fixture-based tests for ci/base-diff.py.
#
# ci/base-diff.py is the only piece of custom logic in this repo (everything
# else is declarative: Containerfile, kargs TOML, systemd units), yet had zero
# test coverage — the only way to check a change to its BLAST_RADIUS regex or
# diff logic was to trigger base-watch.yml against live upstream manifests,
# which isn't reproducible on demand. This runs the script against small,
# committed manifest pairs under tests/fixtures/base-diff/ instead.
#
# Dependency-light by design: plain bash + the script's own `python3`, no new
# Python test framework to declare/install, matching the pattern of
# tests/smoke.sh and tests/boot-check.sh.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
script="${here}/../ci/base-diff.py"
fixtures="${here}/fixtures/base-diff"

fail=0
pass() { printf '  ok   %s\n' "$1"; }
bad() {
    printf '  FAIL %s\n' "$1"
    fail=1
}

assert_contains() {
    local desc="$1" haystack="$2" needle="$3"
    if [[ "${haystack}" == *"${needle}"* ]]; then
        pass "${desc}"
    else
        bad "${desc} (expected to find: ${needle})"
    fi
}

assert_not_contains() {
    local desc="$1" haystack="$2" needle="$3"
    if [[ "${haystack}" != *"${needle}"* ]]; then
        pass "${desc}"
    else
        bad "${desc} (did not expect to find: ${needle})"
    fi
}

echo "== first run, no blast-radius packages in the new manifest =="
out="$(python3 "${script}" /nonexistent-old.manifest "${fixtures}/first-run-non-blast.manifest")"
assert_contains "reports first run" "${out}" "First run — recording baseline."
assert_contains "no blast-radius changes" "${out}" "_No blast-radius package changes._"

echo "== first run does not flag GITHUB_OUTPUT changed=true even with blast-radius packages =="
# Every package in a first-seen manifest looks "added" against an empty
# baseline; first_run must still gate has_changes off so day-one doesn't fire
# a spurious base-watch alert.
gh_out_firstrun="$(mktemp)"
GITHUB_OUTPUT="${gh_out_firstrun}" python3 "${script}" /nonexistent-old.manifest "${fixtures}/new-bump.manifest" >/dev/null
gh_content_firstrun="$(cat "${gh_out_firstrun}")"
rm -f "${gh_out_firstrun}"
assert_contains "GITHUB_OUTPUT changed=false on first run" "${gh_content_firstrun}" "changed=false"

echo "== blast-radius package version bump =="
out="$(python3 "${script}" "${fixtures}/old-baseline.manifest" "${fixtures}/new-bump.manifest")"
assert_contains "reports the qemu-kvm bump" "${out}" "qemu-kvm"
assert_contains "shows old -> new evr" "${out}" "9.0.0-1.fc44.x86_64 → 9.1.0-1.fc44.x86_64"

echo "== non-blast-radius change is ignored =="
out="$(python3 "${script}" "${fixtures}/old-baseline.manifest" "${fixtures}/new-non-blast.manifest")"
assert_contains "no blast-radius changes reported" "${out}" "_No blast-radius package changes._"
assert_not_contains "bash bump not reported" "${out}" "bash"

echo "== blast-radius package added =="
out="$(python3 "${script}" "${fixtures}/old-baseline.manifest" "${fixtures}/new-added.manifest")"
assert_contains "reports an Added section" "${out}" "**Added**"
assert_contains "names swtpm" "${out}" "swtpm"

echo "== blast-radius package removed =="
out="$(python3 "${script}" "${fixtures}/old-baseline.manifest" "${fixtures}/new-removed.manifest")"
assert_contains "reports a Removed section" "${out}" "**Removed**"
assert_contains "names libvirt" "${out}" "libvirt"

echo "== GITHUB_OUTPUT file-write path on a real change =="
gh_out="$(mktemp)"
GITHUB_OUTPUT="${gh_out}" python3 "${script}" "${fixtures}/old-baseline.manifest" "${fixtures}/new-bump.manifest" >/dev/null
gh_content="$(cat "${gh_out}")"
rm -f "${gh_out}"
assert_contains "GITHUB_OUTPUT records changed=true" "${gh_content}" "changed=true"
assert_contains "GITHUB_OUTPUT wraps the report block" "${gh_content}" "__BASE_DIFF_EOF__"
assert_contains "GITHUB_OUTPUT report mentions qemu-kvm" "${gh_content}" "qemu-kvm"

echo
if [[ "${fail}" -ne 0 ]]; then
    echo "BASE-DIFF TESTS FAILED"
    exit 1
fi
echo "All base-diff tests passed."
