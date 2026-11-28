#!/usr/bin/env bash
# tests/test-cloison-isolation.sh -- the Cloison variant must not leak into the
# daily image's inputs or CI.
#
#   bash tests/test-cloison-isolation.sh
#
# 1. The daily Containerfile, build_files/ and system_files/ must exist and
#    contain no reference to "cloison" (case-insensitive), so the daily image's
#    build context and output are independent of the variant. A missing input
#    is a FAILURE (never a vacuous pass).
# 2. NOTE only: .github/workflows/*.yml mentions of cloison are reported. A
#    workflow referencing Containerfile.cloison is a FAILURE (the variant now
#    lives in the private bearyjd/cloison repo; tower has no CI leg for it).
# Exits 1 with file:line for each offender.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

fail=0

echo "== daily inputs exist and do not mention cloison =="
missing=0
for p in Containerfile build_files system_files; do
    if [[ ! -e "${p}" ]]; then
        echo "  FAIL ${p} missing (cannot prove isolation)"
        missing=1
        fail=1
    fi
done
if [[ "${missing}" -eq 0 ]]; then
    hits="$(grep -rniI cloison Containerfile build_files system_files || true)"
    if [[ -n "${hits}" ]]; then
        printf '%s\n' "${hits}" | cut -c1-200 | sed 's/^/  FAIL /'
        fail=1
    else
        echo "  ok   no reference in Containerfile, build_files/, system_files/"
    fi
fi

echo "== Justfile / workflows (notes; a workflow using Containerfile.cloison fails) =="
if [[ ! -f Justfile || ! -d .github/workflows ]]; then
    echo "  FAIL Justfile or .github/workflows missing"
    fail=1
else
    wf_notes="$(grep -rnIi cloison .github/workflows || true)"
    if [[ -n "${wf_notes}" ]]; then
        printf '%s\n' "${wf_notes}" | cut -c1-160 | sed 's/^/  NOTE /'
    fi
    wf_hits="$(grep -rnI 'Containerfile\.cloison' .github/workflows || true)"
    if [[ -n "${wf_hits}" ]]; then
        printf '%s\n' "${wf_hits}" | cut -c1-200 | sed 's/^/  FAIL workflow references Containerfile.cloison: /'
        fail=1
    else
        echo "  ok   no workflow references Containerfile.cloison"
    fi
fi

echo
if [[ "${fail}" -ne 0 ]]; then
    echo "CLOISON ISOLATION TEST FAILED"
    exit 1
fi
echo "Cloison isolation test passed."
exit 0
