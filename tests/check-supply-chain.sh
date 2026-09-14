#!/usr/bin/env bash
# Source-level checks for the repository-scoped image-signature configuration.
# These run before every container build, when the host cannot inspect the
# policy files that will only exist inside the resulting image.
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
policy="${repo_root}/system_files/usr/share/bazzite-tower/containers/policy.json"
registry="${repo_root}/system_files/usr/share/bazzite-tower/containers/registries.d/bazzite-tower.yaml"

# Keep this byte-for-byte aligned with the final-image smoke predicate. It tests
# the complete target rule (including its sole-rule shape and key path), not just
# the presence of a matching identity field.
jq -e '.transports.docker["ghcr.io/bearyjd/bazzite-tower"] | type == "array" and length == 1 and .[0].type == "sigstoreSigned" and .[0].keyPath == "/etc/pki/containers/bazzite-tower-cosign.pub" and .[0].signedIdentity == {"type":"matchRepository"}' "${policy}" >/dev/null

python3 - "${policy}" "${registry}" "${repo_root}/Containerfile" "${repo_root}/cosign.pub" "${repo_root}/.github/workflows/build.yml" <<'PY'
import json
import sys
from pathlib import Path

policy_path, registry_path, containerfile_path, public_key_path, workflow_path = map(Path, sys.argv[1:])
policy = json.loads(policy_path.read_text(encoding="utf-8"))
rules = policy["transports"]["docker"]["ghcr.io/bearyjd/bazzite-tower"]
assert len(rules) == 1
rule = rules[0]
assert rule["type"] == "sigstoreSigned"
assert rule["keyPath"] == "/etc/pki/containers/bazzite-tower-cosign.pub"
assert rule["signedIdentity"] == {"type": "matchRepository"}
assert "use-sigstore-attachments: true" in registry_path.read_text(encoding="utf-8")
assert "COPY cosign.pub /usr/share/bazzite-tower/containers/bazzite-tower-cosign.pub" in containerfile_path.read_text(encoding="utf-8")
key = public_key_path.read_text(encoding="utf-8")
assert key.startswith("-----BEGIN PUBLIC KEY-----") and key.rstrip().endswith("-----END PUBLIC KEY-----")
workflow = workflow_path.read_text(encoding="utf-8")
# metadata-action emits full image references; promotion must not prefix one
# with another registry/name pair.
assert 'podman tag "${candidate}" "${IMAGE_NAME}:${tag}"' in workflow
assert '- name: Verify GitHub provenance' in workflow
assert 'gh attestation verify "oci://${IMAGE_REGISTRY}/${IMAGE_NAME}@${DIGEST}" --repo "${GITHUB_REPOSITORY}"' in workflow
PY

echo "Signature policy source is valid and targets ghcr.io/bearyjd/bazzite-tower."
