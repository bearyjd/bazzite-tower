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

python3 - "${policy}" "${registry}" "${repo_root}/Containerfile" "${repo_root}/cosign.pub" "${repo_root}/.github/workflows/build.yml" "${repo_root}/build_files/build.d/15-signature-policy.sh" "${repo_root}/scripts/install-signature-policy.sh" "${repo_root}/system_files/usr/lib/bootc/install/50-signature-policy.toml" "${repo_root}/renovate.json" <<'PY'
import json
import sys
import tempfile
from pathlib import Path

policy_path, registry_path, containerfile_path, public_key_path, workflow_path, image_installer_path, bootstrap_installer_path, install_config_path, renovate_path = map(Path, sys.argv[1:])
policy = json.loads(policy_path.read_text(encoding="utf-8"))
rules = policy["transports"]["docker"]["ghcr.io/bearyjd/bazzite-tower"]
assert policy["default"] == [{"type": "reject"}]
assert policy["transports"]["docker"][""] == [{"type": "insecureAcceptAnything"}]
assert len(rules) == 1
rule = rules[0]
assert rule["type"] == "sigstoreSigned"
assert rule["keyPath"] == "/etc/pki/containers/bazzite-tower-cosign.pub"
assert rule["signedIdentity"] == {"type": "matchRepository"}
assert "use-sigstore-attachments: true" in registry_path.read_text(encoding="utf-8")
assert "COPY cosign.pub /usr/share/bazzite-tower/containers/bazzite-tower-cosign.pub" in containerfile_path.read_text(encoding="utf-8")
# A literal backslash-n makes jq reject the installed policy even though the
# source policy is valid; both image and host-bootstrap serializers must append
# a real newline.
for installer_path in (image_installer_path, bootstrap_installer_path):
    installer = installer_path.read_text(encoding="utf-8")
    assert 'json.dumps(current, indent=2) + "\\n"' in installer
    assert 'json.dumps(current, indent=2) + "\\\\n"' not in installer
    assert 'current["default"] = requested["default"]' in installer
    assert 'docker.setdefault("", requested["transports"]["docker"][""])' in installer

    program = installer.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]

    def merge(existing):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.json"
            destination = Path(tmp) / "policy.json"
            source.write_text(json.dumps(policy), encoding="utf-8")
            destination.write_text(json.dumps(existing), encoding="utf-8")
            argv = sys.argv
            try:
                sys.argv = ["policy-merge", str(source), str(destination)]
                exec(compile(program, str(installer_path), "exec"), {"__name__": "__main__"})
            finally:
                sys.argv = argv
            return json.loads(destination.read_text(encoding="utf-8"))

    migrated = merge({
        "default": [{"type": "insecureAcceptAnything"}],
        "transports": {
            "docker": {"quay.io/example": [{"type": "reject"}]},
            "atomic": {"example": [{"type": "insecureAcceptAnything"}]},
        },
    })
    assert migrated["default"] == [{"type": "reject"}]
    assert migrated["transports"]["docker"][""] == [{"type": "insecureAcceptAnything"}]
    assert migrated["transports"]["docker"]["quay.io/example"] == [{"type": "reject"}]
    assert migrated["transports"]["atomic"]["example"] == [{"type": "insecureAcceptAnything"}]
    strict_fallback = merge({
        "default": [{"type": "insecureAcceptAnything"}],
        "transports": {"docker": {"": [{"type": "reject"}]}},
    })
    assert strict_fallback["transports"]["docker"][""] == [{"type": "reject"}]
assert install_config_path.read_text(encoding="utf-8").strip().endswith(
    "enforce-container-sigpolicy = true"
)
key = public_key_path.read_text(encoding="utf-8")
assert key.startswith("-----BEGIN PUBLIC KEY-----") and key.rstrip().endswith("-----END PUBLIC KEY-----")
workflow = workflow_path.read_text(encoding="utf-8")
# metadata-action emits full image references; promotion must not prefix one
# with another registry/name pair.
assert 'podman tag "${candidate}" "${IMAGE_NAME}:${tag}"' in workflow
assert '- name: Verify GitHub provenance' in workflow
assert 'gh attestation verify "oci://${IMAGE_REGISTRY}/${IMAGE_NAME}@${DIGEST}" --repo "${GITHUB_REPOSITORY}"' in workflow
assert 'podman save --format oci-archive --output "${archive}"' in workflow
# Syft must scan the pre-push local archive directly. Do not restore the
# sbom-action wrapper: it buffers scanner stdout in Node and leaves the large
# bootc archive scan susceptible to runner OOM. Do not restore its download
# helper either: verify the exact versioned release asset before extraction.
# Serialized cataloging and the external timeout make this gate bounded and
# fail closed.
assert 'uses: anchore/sbom-action@' not in workflow
assert 'uses: anchore/sbom-action/download-syft@' not in workflow
assert 'SYFT_VERSION: v1.51.1' in workflow
assert 'SYFT_SHA256: 8fcb33017a0dc1058298c923c436d19dfa68ae93968e0b423248542e3afb9fc3' in workflow
assert 'https://github.com/anchore/syft/releases/download/${SYFT_VERSION}/syft_${SYFT_VERSION#v}_linux_amd64.tar.gz' in workflow
assert "printf '%s  %s\\n' \"${SYFT_SHA256}\" \"${archive}\" | sha256sum --check --status" in workflow
assert 'tar --extract --gzip --file "${archive}" --directory "${syft_dir}" syft' in workflow
assert "printf 'cmd=%s\\n' \"${syft_dir}/syft\" >> \"${GITHUB_OUTPUT}\"" in workflow
assert 'SYFT_PARALLELISM: "1"' in workflow
assert 'SYFT_CHECK_FOR_APP_UPDATE: "false"' in workflow
assert 'SYFT_JAVASCRIPT_SEARCH_REMOTE_LICENSES: "false"' in workflow
assert 'SYFT_PYTHON_SEARCH_REMOTE_LICENSES: "false"' in workflow
assert 'SYFT_JAVA_USE_NETWORK: "false"' in workflow
assert 'timeout-minutes: 5' in workflow
assert 'timeout --foreground --signal=TERM --kill-after=60s 15m "${SYFT_CMD}" scan' in workflow
assert '--from oci-archive "${archive}" -o spdx-json > "${sbom}"' in workflow
assert 'test -s "${sbom}"' in workflow
assert 'startswith("SPDX-")' in workflow
assert 'timeout-minutes: 18' in workflow
renovate = json.loads(renovate_path.read_text(encoding="utf-8"))
assert any(
    manager.get("datasourceTemplate") == "custom.syft-release-asset"
    and "SYFT_SHA256" in "".join(manager.get("matchStrings", []))
    and "newDigest" in manager.get("autoReplaceStringTemplate", "")
    for manager in renovate["customManagers"]
)
assert renovate["customDatasources"]["syft-release-asset"]["defaultRegistryUrlTemplate"] == "https://api.github.com/repos/anchore/syft/releases/latest"
assert 'image: ${{ env.IMAGE_REGISTRY }}/${{ env.IMAGE_NAME }}@${{ steps.push_candidate.outputs.digest }}' not in workflow
assert workflow.index('- name: Generate SPDX SBOM from local candidate') < workflow.index('- name: Push candidate to GHCR')
assert workflow.index('- name: Attach signed SBOM attestation') < workflow.index('- name: Promote verified candidate tags')
PY

echo "Signature policy source is valid and targets ghcr.io/bearyjd/bazzite-tower."
