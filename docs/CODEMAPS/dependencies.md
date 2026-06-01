<!-- Generated: 2026-05-31 | Files scanned: 5 | Token estimate: ~520 -->
# Dependencies & External Surfaces

## Base image

`ghcr.io/ublue-os/bazzite-nvidia:stable` — Bazzite KDE + proprietary NVIDIA, F44+.
Swap the Containerfile `FROM` → `bazzite-nvidia-open:stable` for the open modules.

## Package repos

| Repo | State | Use |
|---|---|---|
| Fedora (base) | enabled | virt stack, dev tooling |
| `docker-ce` (download.docker.com) | `enabled=0` on disk | install-time only via `--enablerepo=docker-ce-stable`; inert at runtime |

## Registries / external services

- **GHCR** — image publish + pull (the rebase target)
- **download.docker.com** — build-time only (Docker CE packages + gpg key)
- **S3** (rclone, optional) — disk-image upload in `build-disk.yml` (`S3_*` secrets)
- **ArtifactHub** — image metadata/labels (`artifacthub-repo.yml`, labels in build.yml)

## Signing

cosign — `cosign.pub` is tracked; private key via `SIGNING_SECRET` (CI) or
`cosign.key` (local, gitignored). Image is signed by digest.

## GitHub Actions (SHA-pinned, renovate-managed)

`actions/checkout`, `ublue-os/remove-unwanted-software`,
`redhat-actions/buildah-build` + `push-to-registry`, `docker/metadata-action` +
`login-action`, `sigstore/cosign-installer`, `actions/github-script`,
`osbuild/bootc-image-builder-action`, `actions/upload-artifact`.

## Local-dev tooling

`just`, `podman`, bootc-image-builder (`quay.io/centos-bootc/bootc-image-builder`),
qemu (run-vm), `python3` (base-diff.py), `shellcheck`/`shfmt` (`just lint`/`format`).

## Automation

`renovate.json` — pins/updates Actions + base. No application dependency manifest
(this is not an app).
