#!/usr/bin/env bash
# Blast-radius package diff between two digests of the upstream base image.
# Pulls each digest in turn (one at a time, removed after use, to bound runner
# disk), takes `rpm -qa` exactly as base-watch.yml does, and runs ci/base-diff.py
# (its BLAST_RADIUS regex) on the pair. No cheaper manifest source is known: the
# rpm db is only inside the image layers, and ublue publishes no SBOM we rely on.
# Writes a SANITIZED report to <outfile> (ASCII, a tiny safe charset, capped) and
# exits non-zero if either manifest cannot be obtained or looks implausible
# (callers must treat that as "diff not computed" and fail closed).
# Usage: ci/base-blast-diff.sh <image-repo> <current-digest> <new-digest> <outfile>
set -euo pipefail

repo=$1 cur=$2 new=$3 outfile=$4
here=$(dirname "${BASH_SOURCE[0]}")
work=$(mktemp -d)
trap 'rm -rf "${work}"; podman rmi -f "${repo}@${cur}" "${repo}@${new}" > /dev/null 2>&1 || true' EXIT
# Anchors every real bazzite-nvidia-open manifest carries (verified on the live image). qemu/libvirt
# are NOT in the base (our 10-virt-packages.sh layer adds them), so they cannot be required here.
anchors=(kernel-core NetworkManager bootc systemd)

manifest() { # $1 = digest, $2 = output file
    local ref="${repo}@$1" i
    for i in 1 2 3 4; do
        podman pull --quiet "${ref}" > /dev/null && break
        [[ "${i}" == 4 ]] && { echo "::notice::blast-radius: could not pull ${ref}" >&2; return 1; }
        sleep $((2 ** i))
    done
    # Offline, no implicit pull; rpm's own exit status must count (pipefail), not just sort's.
    if ! podman run --rm --network=none --pull=never "${ref}" rpm -qa --qf '%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\n' | sort > "$2"; then
        echo "::notice::blast-radius: rpm -qa failed for ${ref}" >&2
        return 1
    fi
    podman rmi -f "${ref}" > /dev/null 2>&1 || true
    # A truncated or empty manifest would make two bad manifests "identical".
    local a
    if [[ $(wc -l < "$2") -lt 500 ]]; then
        echo "::notice::blast-radius: implausible manifest for ${ref} (<500 lines)" >&2
        return 1
    fi
    for a in "${anchors[@]}"; do
        if ! grep -q "^${a} " "$2"; then
            echo "::notice::blast-radius: implausible manifest for ${ref} (no ${a})" >&2
            return 1
        fi
    done
}

manifest "${cur}" "${work}/old.txt"
manifest "${new}" "${work}/new.txt"
env -u GITHUB_OUTPUT python3 "${here}/base-diff.py" "${work}/old.txt" "${work}/new.txt" > "${work}/report.txt"
# Upstream-controlled names/versions: arrow to ASCII, then keep a safe charset only
# (no @ [ ] < | & $ \), cap lines and size so it is safe in GITHUB_OUTPUT and a PR body.
sed 's/\xe2\x86\x92/->/g' "${work}/report.txt" | LC_ALL=C tr -cd '\n0-9A-Za-z._+:~^ ()`*>/=,-' \
    | head -n 300 | head -c 20000 > "${outfile}"
[[ -s "${outfile}" ]] || { echo "::notice::blast-radius: empty report" >&2; exit 1; }
