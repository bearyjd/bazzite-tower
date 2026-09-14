#!/usr/bin/env bash
# Install bazzite-tower's repository-scoped sigstore policy on the current
# bootc host. Run this from a trusted checkout before the first bootc switch:
# the policy inside a target image cannot authenticate that image retroactively.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Run with sudo: sudo $0" >&2
    exit 2
fi

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
policy_source="${repo_root}/system_files/usr/share/bazzite-tower/containers/policy.json"
registry_source="${repo_root}/system_files/usr/share/bazzite-tower/containers/registries.d/bazzite-tower.yaml"
key_source="${repo_root}/cosign.pub"

for source in "${policy_source}" "${registry_source}" "${key_source}"; do
    [[ -f "${source}" ]] || { echo "Missing source file: ${source}" >&2; exit 1; }
done

install -D -m 0644 "${key_source}" /etc/pki/containers/bazzite-tower-cosign.pub
install -D -m 0644 "${registry_source}" /etc/containers/registries.d/bazzite-tower.yaml

python3 - "${policy_source}" /etc/containers/policy.json <<'PY'
import json
import os
import sys
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
requested = json.loads(source.read_text(encoding="utf-8"))
if destination.exists():
    current = json.loads(destination.read_text(encoding="utf-8"))
else:
    current = {"default": [{"type": "insecureAcceptAnything"}]}
transports = current.setdefault("transports", {})
docker = transports.setdefault("docker", {})
docker["ghcr.io/bearyjd/bazzite-tower"] = requested["transports"]["docker"]["ghcr.io/bearyjd/bazzite-tower"]
destination.parent.mkdir(parents=True, exist_ok=True)
temporary = destination.with_suffix(".json.tmp")
temporary.write_text(json.dumps(current, indent=2) + "\\n", encoding="utf-8")
os.replace(temporary, destination)
PY

echo "Installed the bazzite-tower sigstore policy and registry attachment configuration."
