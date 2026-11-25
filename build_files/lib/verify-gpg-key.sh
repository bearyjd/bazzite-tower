#!/usr/bin/env bash
# Sourced by build.d/30-docker-ce.sh (and tests/test-docker-ce-gpg-pin.sh).
# Defines functions only; sourcing has no side effects. Not in build.d/, so the
# build.sh glob never executes it.

# verify_gpg_key <key-file> <pinned-fingerprint> <output-file>
#
# Succeeds only if <key-file> holds exactly ONE primary (`pub:`) key whose full
# 40-hex fingerprint equals the pin. rpm imports every key in a gpgkey= file but
# a naive check sees only the first, so a second key must be a hard failure.
# On success the single verified key is re-exported (armored) to <output-file>,
# so nothing but the verified key is ever installed. Uses a private GNUPGHOME
# (the build container has no writable ~/.gnupg). No network.
verify_gpg_key() {
    local keyfile=$1 pin=$2 out=$3 home rc=0
    home=$(mktemp -d) || return 1
    _verify_gpg_key_in "${home}" "${keyfile}" "${pin}" "${out}" || rc=$?
    rm -rf "${home}"
    return "${rc}"
}

_verify_gpg_key_in() {
    local home=$1 keyfile=$2 pin=$3 out=$4 colons count fpr
    if [[ ! ${pin} =~ ^[0-9A-F]{40}$ ]]; then
        echo "verify_gpg_key: pinned fingerprint must be 40 uppercase hex digits" >&2
        return 1
    fi
    if [[ ! -s ${keyfile} ]]; then
        echo "verify_gpg_key: key file missing or empty: ${keyfile}" >&2
        return 1
    fi
    colons=$(GNUPGHOME="${home}" gpg --batch --no-tty --show-keys --with-colons "${keyfile}" 2>/dev/null) || {
        echo "verify_gpg_key: gpg could not parse ${keyfile}" >&2
        return 1
    }
    count=$(awk -F: '$1 == "pub" { n++ } END { print n + 0 }' <<<"${colons}")
    if [[ ${count} -ne 1 ]]; then
        echo "verify_gpg_key: expected exactly 1 primary key in ${keyfile}, found ${count}" >&2
        return 1
    fi
    # The first fpr: record after pub: is the primary key's fingerprint.
    fpr=$(awk -F: '$1 == "pub" { p = 1; next } p && $1 == "fpr" { print $10; exit }' <<<"${colons}")
    if [[ ${fpr} != "${pin}" ]]; then
        echo "verify_gpg_key: fingerprint mismatch: got '${fpr}', pinned '${pin}'" >&2
        return 1
    fi
    GNUPGHOME="${home}" gpg --batch --no-tty --import "${keyfile}" >/dev/null 2>&1 || {
        echo "verify_gpg_key: gpg import failed" >&2
        return 1
    }
    GNUPGHOME="${home}" gpg --batch --no-tty --export --armor "${pin}" >"${out}" 2>/dev/null || {
        echo "verify_gpg_key: gpg export failed" >&2
        return 1
    }
    [[ -s ${out} ]] || {
        echo "verify_gpg_key: exported key is empty" >&2
        return 1
    }
}
