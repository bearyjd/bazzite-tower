#!/usr/bin/env bash
# Fixture tests for the deterministic installed-RPM SPDX predicate generator.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
generator="${repo_root}/ci/rpm-inventory-spdx.py"
fixtures="${repo_root}/tests/fixtures/rpm-inventory-spdx"
tmpdir="$(mktemp -d)"
trap 'rm -rf "${tmpdir}"' EXIT

created='2026-09-14T00:00:00Z'
python3 "${generator}" --input "${fixtures}/installed-rpms.tsv" \
    --output "${tmpdir}/first.spdx.json" --created "${created}"
python3 "${generator}" --input "${fixtures}/installed-rpms-reordered.tsv" \
    --output "${tmpdir}/reordered.spdx.json" --created "${created}"
cmp "${tmpdir}/first.spdx.json" "${tmpdir}/reordered.spdx.json"

python3 - "${tmpdir}/first.spdx.json" <<'PY'
import json
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
assert document["spdxVersion"] == "SPDX-2.3"
assert document["SPDXID"] == "SPDXRef-DOCUMENT"
assert document["creationInfo"]["created"] == "2026-09-14T00:00:00Z"
assert document["creationInfo"]["creators"] == ["Tool: bazzite-tower-rpm-inventory-spdx-1.0.0"]
assert document["name"] == "bazzite-tower installed RPM inventory"
assert len(document["packages"]) == 3
assert [package["name"] for package in document["packages"]] == ["bash", "coreutils", "zlib"]
assert document["packages"][2]["versionInfo"] == "1:1.3.1-6.fc44.x86_64"
assert len(document["relationships"]) == 3
assert all(relationship["relationshipType"] == "DESCRIBES" for relationship in document["relationships"])
assert len({package["SPDXID"] for package in document["packages"]}) == 3
PY

python3 "${generator}" --input "${fixtures}/installed-rpms.tsv" \
    --output "${tmpdir}/offset.spdx.json" --created '2026-09-14T01:00:00+01:00'
python3 - "${tmpdir}/offset.spdx.json" <<'PY'
import json
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
assert document["creationInfo"]["created"] == "2026-09-14T00:00:00Z"
PY

python3 "${generator}" --input "${fixtures}/installed-rpms.tsv" \
    --output "${tmpdir}/later.spdx.json" --created '2026-09-15T00:00:00Z'
python3 - "${tmpdir}/first.spdx.json" "${tmpdir}/later.spdx.json" <<'PY'
import json
import sys

first = json.load(open(sys.argv[1], encoding="utf-8"))
later = json.load(open(sys.argv[2], encoding="utf-8"))
assert first["documentNamespace"] != later["documentNamespace"]
PY

for fixture in empty.tsv duplicate.tsv malformed.tsv; do
    if python3 "${generator}" --input "${fixtures}/${fixture}" \
        --output "${tmpdir}/${fixture}.json" --created "${created}"; then
        echo "expected ${fixture} to be rejected" >&2
        exit 1
    fi
done

echo "RPM inventory SPDX fixtures passed."
