#!/usr/bin/env bash
# Install a repository-scoped sigstore policy without discarding the base
# image's existing policy rules. The bootstrap helper installs the same files
# on the current host before its first switch.
set -euo pipefail

policy_source=/usr/share/bazzite-tower/containers/policy.json
policy_destination=/etc/containers/policy.json
registry_source=/usr/share/bazzite-tower/containers/registries.d/bazzite-tower.yaml

install -D -m 0644 /usr/share/bazzite-tower/containers/bazzite-tower-cosign.pub /etc/pki/containers/bazzite-tower-cosign.pub
install -D -m 0644 "${registry_source}" /etc/containers/registries.d/bazzite-tower.yaml

python3 - "${policy_source}" "${policy_destination}" <<'PY'
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
temporary.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
os.replace(temporary, destination)
PY
