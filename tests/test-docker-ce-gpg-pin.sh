#!/usr/bin/env bash
# Offline test for the Docker CE GPG key pin (build_files/lib/verify-gpg-key.sh
# and build_files/build.d/30-docker-ce.sh). Uses THROWAWAY keys generated here;
# never touches the network or the real Docker key.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
script="${repo_root}/build_files/build.d/30-docker-ce.sh"
lib="${repo_root}/build_files/lib/verify-gpg-key.sh"

tmp=$(mktemp -d)
trap 'rm -rf "${tmp}"' EXIT
fail=0
ok() { printf '  ok   %s\n' "$1"; }
bad() { printf '  FAIL %s\n' "$1"; fail=1; }

# --- Static assertions on the build script, comments stripped so a mention in
# a comment cannot satisfy them.
code="${tmp}/30-docker-ce.nocomments"
sed -E 's/^[[:space:]]*#.*$//' "${script}" >"${code}"

assert_code() { # <description> <fixed-or-regex pattern>
    if grep -Eq -- "$2" "${code}"; then ok "$1"; else bad "$1"; fi
}
assert_no_code() {
    if grep -Eq -- "$2" "${code}"; then bad "$1"; else ok "$1"; fi
}
assert_code "pins a 40-hex fingerprint" '^DOCKER_GPG_FPR=[0-9A-F]{40}$'
assert_code "verifies via verify_gpg_key with the pin" 'verify_gpg_key "\$\{docker_key\}" "\$\{DOCKER_GPG_FPR\}"'
assert_code "mismatch exits 1" 'exit 1'
assert_code "curl is https-only with retry" "curl -fsSL --retry 3 --proto '=https' --proto-redir '=https'"
assert_code "installs key to /etc/pki/rpm-gpg" 'DOCKER_KEY_DEST=/etc/pki/rpm-gpg/RPM-GPG-KEY-docker-ce'
assert_no_code "no gpgkey=http remains" '^gpgkey=http'
assert_no_code "no || true on verification" 'verify_gpg_key.*\|\|'
assert_no_code "no gpgcheck=0" 'gpgcheck=0'
n_sections=$(grep -c '^\[docker-ce-' "${code}")
if [[ ${n_sections} -eq 1 ]] && grep -qx '\[docker-ce-stable\]' "${code}" && grep -qx 'enabled=0' "${code}"; then
    ok "exactly one repo section, [docker-ce-stable], enabled=0"
else
    bad "expected only [docker-ce-stable] with enabled=0 (sections=${n_sections})"
fi
n_gpgkey=$(grep -c '^gpgkey=file:///etc/pki/rpm-gpg/RPM-GPG-KEY-docker-ce$' "${code}")
n_check=$(grep -c '^gpgcheck=1$' "${code}")
if [[ ${n_sections} -gt 0 && ${n_sections} -eq ${n_gpgkey} && ${n_sections} -eq ${n_check} ]]; then
    ok "every repo section has gpgcheck=1 and the local gpgkey (${n_sections})"
else
    bad "repo sections=${n_sections} gpgkey=${n_gpgkey} gpgcheck=${n_check}"
fi

# --- Functional tests (need gpg).
if ! command -v gpg >/dev/null 2>&1; then
    echo "  SKIP functional tests: gpg not available" >&2
    exit "${fail}"
fi

# shellcheck source=build_files/lib/verify-gpg-key.sh
source "${lib}"

gen() { # <name> -> ${tmp}/<name>.asc ; prints fingerprint
    local home="${tmp}/home-$1" fpr
    install -d -m 700 "${home}"
    GNUPGHOME="${home}" gpg --batch --no-tty --pinentry-mode loopback --passphrase '' \
        --quick-generate-key "throwaway $1 <$1@example.invalid>" ed25519 sign never >/dev/null 2>&1
    fpr=$(GNUPGHOME="${home}" gpg --batch --with-colons --list-keys 2>/dev/null |
        awk -F: '$1 == "fpr" { print $10; exit }')
    GNUPGHOME="${home}" gpg --batch --armor --export "${fpr}" >"${tmp}/$1.asc"
    printf '%s' "${fpr}"
}

fpr_a=$(gen a)
fpr_b=$(gen b)
if [[ ! ${fpr_a} =~ ^[0-9A-F]{40}$ || ! ${fpr_b} =~ ^[0-9A-F]{40}$ ]]; then
    bad "could not generate throwaway keys (gpg unusable here)"
    exit 1
fi
cat "${tmp}/a.asc" "${tmp}/b.asc" >"${tmp}/two.asc"
: >"${tmp}/empty.asc"
echo "this is not a key" >"${tmp}/garbage.asc"
out="${tmp}/out.asc"

expect_pass() { # <description> <keyfile> <pin>
    rm -f "${out}"
    if verify_gpg_key "$2" "$3" "${out}" 2>"${tmp}/err" && [[ -s ${out} ]]; then ok "$1"; else bad "$1"; fi
}
expect_fail() {
    rm -f "${out}"
    if verify_gpg_key "$2" "$3" "${out}" 2>"${tmp}/err"; then bad "$1"; else ok "$1"; fi
}

expect_pass "matching pin passes" "${tmp}/a.asc" "${fpr_a}"
# The installed key is exactly the verified one.
install -d -m 700 "${tmp}/chk"
if [[ $(GNUPGHOME="${tmp}/chk" gpg --batch --show-keys --with-colons "${out}" 2>/dev/null |
    awk -F: '$1 == "fpr" { print $10; exit }') == "${fpr_a}" ]]; then
    ok "exported key is the verified key"
else bad "exported key is the verified key"; fi
expect_fail "wrong pin fails" "${tmp}/a.asc" "${fpr_b}"
# One hex digit off (first digit flipped).
flipped="$([[ ${fpr_a:0:1} == A ]] && echo B || echo A)${fpr_a:1}"
expect_fail "pin off by one hex digit fails" "${tmp}/a.asc" "${flipped}"
expect_fail "short pin fails" "${tmp}/a.asc" "${fpr_a:0:16}"
expect_fail "file with two keys fails (pin = first key)" "${tmp}/two.asc" "${fpr_a}"
expect_fail "file with two keys fails (pin = second key)" "${tmp}/two.asc" "${fpr_b}"
expect_fail "empty file fails" "${tmp}/empty.asc" "${fpr_a}"
expect_fail "garbage file fails" "${tmp}/garbage.asc" "${fpr_a}"
expect_fail "missing file fails" "${tmp}/nope.asc" "${fpr_a}"

# Verification must not touch the caller's GNUPGHOME / HOME.
home_probe="${tmp}/probe-home"
install -d "${home_probe}"
HOME="${home_probe}" verify_gpg_key "${tmp}/a.asc" "${fpr_a}" "${out}" 2>/dev/null || true
if [[ -e ${home_probe}/.gnupg ]]; then bad "verification leaves \$HOME/.gnupg untouched"; else ok "verification leaves \$HOME/.gnupg untouched"; fi

exit "${fail}"
