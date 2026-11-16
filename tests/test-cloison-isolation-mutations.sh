#!/usr/bin/env bash
# tests/test-cloison-isolation-mutations.sh -- proves tests/test-cloison-isolation.sh
# can fail. Each mutation is applied to a TEMP COPY of the tree (never the
# working tree) and the isolation test must then exit non-zero. The unmutated
# copy must pass first.
#
#   bash tests/test-cloison-isolation-mutations.sh
set -uo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" || exit 1
tmp="$(mktemp -d)" || exit 1
trap 'rm -rf "${tmp}"' EXIT

fail=0
ok()  { printf '  ok   %s\n' "$1"; }
bad() { printf '  FAIL %s\n' "$1"; fail=1; }

# fresh <name>: copy the files the isolation test reads into ${tmp}/<name>.
fresh() {
    local d="${tmp}/$1"
    mkdir -p "${d}/tests" "${d}/.github" || exit 1
    cp -R "${root}/Containerfile" "${root}/Containerfile.cloison" "${root}/Justfile" \
        "${root}/build_files" "${root}/system_files" "${d}/" || exit 1
    cp -R "${root}/.github/workflows" "${d}/.github/" || exit 1
    cp "${root}/tests/test-cloison-isolation.sh" "${d}/tests/" || exit 1
}

# expect_fail <name> <description>: isolation test in ${tmp}/<name> must fail.
expect_fail() {
    if bash "${tmp}/$1/tests/test-cloison-isolation.sh" >/dev/null 2>&1; then
        bad "$2 (isolation test still passed)"
    else
        ok "$2 (isolation test failed as required)"
    fi
}

echo "== baseline =="
fresh base
if bash "${tmp}/base/tests/test-cloison-isolation.sh" >/dev/null 2>&1; then
    ok "unmutated copy passes"
else
    bad "unmutated copy does not pass; mutations below are meaningless"
fi

echo "== mutations (temp copies only) =="
fresh m1
printf '# cloison\n' >> "${tmp}/m1/Containerfile"
expect_fail m1 "cloison reference in daily Containerfile"

fresh m2
sed -i 's|^FROM .*|FROM ghcr.io/example/base:latest|' "${tmp}/m2/Containerfile.cloison"
expect_fail m2 "registry FROM in Containerfile.cloison"

fresh m3
printf 'jobs:\n  b:\n    steps:\n      - run: podman build -f Containerfile.cloison .\n' > "${tmp}/m3/.github/workflows/cloison-ci.yml"
expect_fail m3 "workflow referencing Containerfile.cloison"

fresh m4
rm -r "${tmp}/m4/build_files"
expect_fail m4 "missing build_files/"

echo
if [[ "${fail}" -ne 0 ]]; then
    echo "CLOISON MUTATION TEST FAILED"
    exit 1
fi
echo "Cloison mutation test passed."
exit 0
