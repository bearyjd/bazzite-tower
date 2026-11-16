#!/usr/bin/env bash
# tests/test-cloison-isolation.sh -- the Cloison variant must not leak into the
# daily image's inputs, and Containerfile.cloison must not name its own base.
#
#   bash tests/test-cloison-isolation.sh
#
# 1. The daily Containerfile, build_files/ and system_files/ contain no
#    reference to "cloison" (case-insensitive), so the daily image's build
#    context and output are independent of the variant.
# 2. Every FROM in Containerfile.cloison is `${PARENT_IMAGE}` (optionally with
#    AS <name>) or `scratch`: the base only ever arrives as a digest argument.
# Exits 1 with file:line for each offender.
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

fail=0

echo "== daily inputs do not mention cloison =="
hits="$(grep -rniI cloison Containerfile build_files system_files 2>/dev/null || true)"
if [[ -n "${hits}" ]]; then
    printf '%s\n' "${hits}" | cut -c1-200 | sed 's/^/  FAIL /'
    fail=1
else
    echo "  ok   no reference in Containerfile, build_files/, system_files/"
fi

echo "== Containerfile.cloison FROM lines =="
if [[ ! -f Containerfile.cloison ]]; then
    echo "  FAIL Containerfile.cloison missing"
    fail=1
else
    froms="$(grep -nEi '^[[:space:]]*FROM[[:space:]]' Containerfile.cloison || true)"
    if [[ -z "${froms}" ]]; then
        echo "  FAIL Containerfile.cloison has no FROM"
        fail=1
    fi
    from_re='^[[:space:]]*FROM[[:space:]]+(\$\{PARENT_IMAGE\}|scratch)([[:space:]]+[Aa][Ss][[:space:]]+[A-Za-z0-9_.-]+)?[[:space:]]*$'
    while IFS= read -r line; do
        [[ -z "${line}" ]] && continue
        body="${line#*:}"
        if [[ "${body}" =~ ${from_re} ]]; then
            echo "  ok   Containerfile.cloison:${line}"
        else
            echo "  FAIL Containerfile.cloison:${line} (FROM must be \${PARENT_IMAGE} or scratch)"
            fail=1
        fi
    done <<< "${froms}"
fi

echo
if [[ "${fail}" -ne 0 ]]; then
    echo "CLOISON ISOLATION TEST FAILED"
    exit 1
fi
echo "Cloison isolation test passed."
exit 0
