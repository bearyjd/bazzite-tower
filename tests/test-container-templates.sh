#!/usr/bin/env bash
# Static contracts for inert rootless examples. No container engine is required.
set -euo pipefail

repo_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
root="${repo_root}/system_files/usr/share/bazzite-tower/examples/containers"
quadlet="${root}/quadlet/app.container.example"
compose="${root}/compose/compose.yaml.example"

grep -qx 'UserNS=keep-id' "${quadlet}"
grep -qx 'ReadOnly=true' "${quadlet}"
grep -qx 'NoNewPrivileges=true' "${quadlet}"
grep -qx 'DropCapability=all' "${quadlet}"
if grep -Eiq '^(Privileged|Network)=host' "${quadlet}"; then
    echo "Quadlet example must not use privileged or host networking" >&2
    exit 1
fi
python3 - "${compose}" <<'PY'
import sys
import yaml

with open(sys.argv[1], encoding="utf-8") as handle:
    document = yaml.safe_load(handle)
app = document["services"]["app"]
assert app["read_only"] is True
assert "no-new-privileges:true" in app["security_opt"]
assert app["cap_drop"] == ["ALL"]
assert document["networks"]["app"]["internal"] is True
assert "app_secret" in app["secrets"]
PY
echo "rootless container templates: pass"
