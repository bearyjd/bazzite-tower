#!/usr/bin/env bash
# Move the Containerfile's digest-pinned BASE_IMAGE to the current upstream
# bazzite-nvidia-open:<tag> (default: stable). Edits the file in place and
# reports old/new digest and upstream version via $GITHUB_OUTPUT (stdout if unset).
# Used by .github/workflows/base-bump.yml, which opens the PR. It also reports
# automerge=true|false + reason (ci/base-bump-guard.py): true only when the
# Fedora major is unchanged, the kernel is same-or-newer than the current pin
# and the blast-radius package diff (ci/base-blast-diff.sh) is empty; it also
# reports that diff as the multiline `blast_report`. Anything else (or any lookup/parse failure) is false and a human merges.
# Usage: ci/base-bump.sh [Containerfile]
set -euo pipefail

containerfile=${1:-Containerfile}
repo=ghcr.io/ublue-os/bazzite-nvidia-open
tag=${BASE_TAG:-stable}
out=${GITHUB_OUTPUT:-/dev/stdout}

current=$(sed -n 's/^ARG BASE_IMAGE=.*@\(sha256:[0-9a-f]\{64\}\)$/\1/p' "${containerfile}")
if [[ -z "${current}" ]]; then
    echo "::error::no digest-pinned 'ARG BASE_IMAGE=...@sha256:...' line in ${containerfile}"
    exit 1
fi

info=$(skopeo inspect --retry-times 4 "docker://${repo}:${tag}")
latest=$(jq -r '.Digest' <<< "${info}")
version=$(jq -r '.Labels["org.opencontainers.image.version"] // "unknown"' <<< "${info}")
# Upstream-controlled label: keep it to a safe charset before it reaches $GITHUB_OUTPUT.
version=$(tr -cd '0-9A-Za-z._-' <<< "${version}")
version=${version:-unknown}
# Kernel as recorded by ostree/bootc in the image labels; sanitized like version.
kernel=$(jq -r '.Labels["ostree.linux"] // ""' <<< "${info}" | tr -cd '0-9A-Za-z._-')
kernel=${kernel%.x86_64} # the label carries the arch; the guard's kernel scheme does not
if [[ ! "${latest}" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    echo "::error::unexpected digest from skopeo: '${latest}'"
    exit 1
fi

{
    echo "current=${current}"
    echo "latest=${latest}"
    echo "version=${version}"
} >> "${out}"

if [[ "${latest}" == "${current}" ]]; then
    echo "changed=false" >> "${out}"
    exit 0
fi

# Automerge guard: compare the new base to the current pin. Fails closed.
guard_script=$(dirname "${BASH_SOURCE[0]}")/base-bump-guard.py
if ! cur_info=$(skopeo inspect --retry-times 4 "docker://${repo}@${current}" 2> /dev/null); then
    echo "::notice::guard: skopeo lookup of the CURRENT pin ${repo}@${current} failed; automerge=false" >&2
    cur_info=
fi
cur_version=$(jq -r '.Labels["org.opencontainers.image.version"] // ""' <<< "${cur_info}" 2> /dev/null | tr -cd '0-9A-Za-z._-' || true)
cur_kernel=$(jq -r '.Labels["ostree.linux"] // ""' <<< "${cur_info}" 2> /dev/null | tr -cd '0-9A-Za-z._-' || true)
cur_kernel=${cur_kernel%.x86_64}
# Blast-radius package diff (pulls both digests); failure => no report => guard says false.
blast_file=$(mktemp)
trap 'rm -f "${blast_file}"' EXIT
if ! "$(dirname "${BASH_SOURCE[0]}")/base-blast-diff.sh" "${repo}" "${current}" "${latest}" "${blast_file}"; then
    echo "::notice::guard: blast-radius diff could not be computed; automerge=false" >&2
    : > "${blast_file}"
fi
if ! guard=$(python3 "${guard_script}" --blast-report "${blast_file}" --current-version "${cur_version}" --current-kernel "${cur_kernel}" \
    --new-version "${version}" --new-kernel "${kernel}"); then
    echo "::notice::guard: ci/base-bump-guard.py itself failed to run; automerge=false" >&2
    guard=
fi
automerge=$(sed -n 's/^automerge=\(true\|false\)$/\1/p' <<< "${guard}")
if [[ "${automerge}" != "true" ]]; then automerge=false; fi
blast_eof="BLAST_EOF_${RANDOM}${RANDOM}${RANDOM}"
reason=$(sed -n 's/^reason=//p' <<< "${guard}" | head -n1 | tr -cd '0-9A-Za-z._ ():,>/=-')
{
    echo "automerge=${automerge}"
    echo "reason=${reason:-guard produced no decision}"
    echo "blast_report<<${blast_eof}"
    if [[ -s "${blast_file}" ]]; then cat "${blast_file}"; echo; else echo "_Blast-radius diff could not be computed._"; fi
    echo "${blast_eof}"
} >> "${out}"

sed -i "s|^\\(ARG BASE_IMAGE=.*@\\)${current}\$|\\1${latest}|" "${containerfile}"
grep -q "@${latest}\$" "${containerfile}" || { echo "::error::digest replace failed"; exit 1; }
echo "changed=true" >> "${out}"
