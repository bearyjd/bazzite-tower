#!/usr/bin/env bash
# The Docker CE repo signing key must be verified against a pinned fingerprint,
# not trusted on first use over TLS via a remote gpgkey= URL.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
script="${repo_root}/build_files/build.d/30-docker-ce.sh"
fingerprint=060A61C51B558A7F742B77AAC52FEB6B621E9F35

if grep -Eq '^gpgkey=https?://' "${script}"; then
    echo "FAIL: ${script} still trusts a remote gpgkey URL on first use" >&2
    exit 1
fi
if ! grep -q "${fingerprint}" "${script}"; then
    echo "FAIL: ${script} does not pin Docker key fingerprint ${fingerprint}" >&2
    exit 1
fi
if ! grep -q '^gpgkey=file://' "${script}"; then
    echo "FAIL: ${script} repo sections must use a locally verified gpgkey" >&2
    exit 1
fi
echo "docker-ce gpg pin: ok"
