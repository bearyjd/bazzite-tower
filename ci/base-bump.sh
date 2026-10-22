#!/usr/bin/env bash
# Move the Containerfile's digest-pinned BASE_IMAGE to the current upstream
# bazzite-nvidia-open:<tag> (default: stable). Edits the file in place and
# reports old/new digest and upstream version via $GITHUB_OUTPUT (stdout if unset).
# Used by .github/workflows/base-bump.yml, which opens the PR; a human merges it.
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

sed -i "s|^\\(ARG BASE_IMAGE=.*@\\)${current}\$|\\1${latest}|" "${containerfile}"
grep -q "@${latest}\$" "${containerfile}" || { echo "::error::digest replace failed"; exit 1; }
echo "changed=true" >> "${out}"
