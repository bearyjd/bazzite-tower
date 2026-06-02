# Plan: Titanoboa-based installable ISO workflow

## Summary
Add a CI workflow (plus a local recipe and docs) that builds a **bootable, installable live ISO** for `bazzite-tower` using **titanoboa** (ublue's live-ISO toolchain), replacing the upstream-broken `bootc-image-builder` anaconda-iso path that was removed in commit `0f012e4`. The ISO lets someone do a clean bare-metal install without an existing bootc system to `bootc switch` from.

## User Story
As someone doing a clean bare-metal install of bazzite-tower, I want a downloadable bootable ISO, so that I can install it on a fresh machine without first having another bootc OS to rebase from.

## Problem → Solution
Today there is **no working installable media**. `build-disk.yml` builds only a qcow2; the anaconda-iso path is dead (bootc-image-builder#1188 + ublue-os/bazzite#3418). → A `titanoboa`-built live ISO, the same toolchain ublue/Bazzite use for their real ISOs.

## Metadata
- **Complexity**: Large (new CI subsystem + external integration + an empirically-resolved unknown)
- **Source PRD**: N/A (free-form follow-up from the disk-build investigation)
- **PRD Phase**: N/A
- **Estimated Files**: 4 on the simple path (new workflow, Justfile, README, ci-cd codemap); up to 7 if the spike forces the payload-builder fallback (+ `installer/Containerfile`, a flatpaks list, and/or `system_files/usr/lib/bootc-image-builder/iso.yaml`)

---

## UX Design

### Before
```
Fresh machine, no bootc OS yet
   └─> no bazzite-tower install path. Must first install some other
       bootc distro, then `bootc switch ghcr.io/bearyjd/bazzite-tower:latest`.
   CI artifacts: qcow2 only (VM testing), no ISO.
```

### After
```
Fresh machine
   └─> download bazzite-tower-<tag>-live-amd64.iso  (CI artifact / S3)
   └─> flash to USB, boot, install. Done.
   CI: `build-iso.yml` (workflow_dispatch) produces a signed, checksummed ISO.
```

### Interaction Changes
| Touchpoint | Before | After | Notes |
|---|---|---|---|
| Fresh install | Not possible without another bootc OS | Boot the ISO and install | The actual user-facing win |
| CI disk artifacts | qcow2 only | qcow2 (build-disk.yml) + ISO (build-iso.yml) | Two separate workflows; build-disk.yml unchanged |
| Local ISO build | `just build-iso` (BIB, broken) | `just build-iso-live` (titanoboa) | Old BIB iso recipes stay but are known-broken |

---

## Mandatory Reading

| Priority | File | Lines | Why |
|---|---|---|---|
| P0 | `ublue-os/titanoboa` → `action.yml` (github.com/ublue-os/titanoboa) | all | The action contract: inputs are exactly `image-ref` (required) + `iso-dest` (default `${{ github.workspace }}/output.iso`); output `iso-dest`; composite, runs `main.sh` with `--cap-add sys_admin --security-opt label=disable` |
| P0 | `ublue-os/bazzite` → `.github/workflows/build_iso.yml` | all | Canonical titanoboa usage on our exact base family. Source of: disk-space loopback, payload-builder pattern, signing, checksum, artifact/R2 upload. NOTE: uses a fork `Zeglius/titanoboa@revamp-pr` |
| P0 | `.github/workflows/build.yml` (this repo) | 17-23, 46-50, 151-198, 206-244 | Mirror: image-ref env block, lowercase-owner prep, cosign install+sign-by-digest gated on `SIGNING_SECRET`, and the open/close `ci-failure` issue-on-failure pattern |
| P1 | `.github/workflows/build-disk.yml` (this repo) | all | Mirror: `workflow_dispatch` + `platform` input, `ublue-os/remove-unwanted-software` free-space step, artifact upload (`actions/upload-artifact@…v7`), S3 rclone upload block, SHA-pinned action style |
| P1 | `disk_config/iso-kde.toml` (this repo) | all | The kept-but-unused BIB ISO config; the live ISO does not use it, but the desktop intent (KDE) carries over |
| P2 | `ublue-os/bazzite` → `installer/` (Containerfile + `*_flatpaks` lists) | all | ONLY if the spike (Task 1) shows the plain published image fails titanoboa's contract: this is the payload-builder pattern to port |
| P2 | `~/.claude/.../memory/bazzite-tower-disk-build-constraints.md` | all | Why anaconda-iso was dropped; do not re-attempt BIB ISO |

## External Documentation

| Topic | Source | Key Takeaway |
|---|---|---|
| titanoboa action | https://github.com/ublue-os/titanoboa | `uses: ublue-os/titanoboa@<sha>`, inputs `image-ref` + `iso-dest`, output `output.iso`. Live ISO of a bootc image. Apache-2.0 |
| ISO contract v0.1.0 | titanoboa README | The image must ship `/usr/lib/bootc-image-builder/iso.yaml`, kernel + `initramfs.img` under `/usr/lib/modules/*`, UEFI binaries in `/boot/efi/EFI/$VENDOR`, grub modules in `/usr/lib/grub/i386-pc`. **Whether bazzite-nvidia ships these is the spike question.** |
| Bazzite ISO build | https://github.com/ublue-os/bazzite/blob/main/.github/workflows/build_iso.yml | Builds a `localhost/payload` from `installer/` first, then feeds THAT to titanoboa — implies the contract/payload is assembled by `installer/`, not necessarily the base image |
| BIB anaconda-iso is dead | github.com/osbuild/bootc-image-builder/issues/1188, github.com/ublue-os/bazzite/issues/3418 | Do not revisit BIB anaconda-iso; titanoboa is the path |

---

## Patterns to Mirror

Follow these exactly. All snippets are from THIS repo unless marked otherwise.

### IMAGE_REF_ENV
// SOURCE: .github/workflows/build.yml:17-23
```yaml
env:
  IMAGE_NAME: "${{ github.event.repository.name }}"
  IMAGE_REGISTRY: "ghcr.io/${{ github.repository_owner }}"
  DEFAULT_TAG: "latest"
```

### LOWERCASE_OWNER_PREP
// SOURCE: .github/workflows/build.yml:46-50
```yaml
- name: Prepare environment
  run: |
    echo "IMAGE_REGISTRY=${IMAGE_REGISTRY,,}" >> ${GITHUB_ENV}
    echo "IMAGE_NAME=${IMAGE_NAME,,}" >> ${GITHUB_ENV}
```

### FREE_DISK_SPACE
// SOURCE: .github/workflows/build-disk.yml (Maximize build space step)
```yaml
- name: Maximize build space
  uses: ublue-os/remove-unwanted-software@695eb75bc387dbcd9685a8e72d23439d8686cba6
```
// Bazzite ALSO mounts a 70G btrfs loopback at /var/lib/containers/storage when
// /mnt exists (build_iso.yml). Port that if the ISO build runs out of space.

### COSIGN_SIGN (this repo signs the image by digest; for a blob use sign-blob)
// SOURCE: .github/workflows/build.yml:182-198 (install) ; bazzite build_iso.yml (sign-blob)
```yaml
- name: Install Cosign
  uses: sigstore/cosign-installer@6f9f17788090df1f26f669e9d70d6ae9567deba6 # v4.1.2
  if: env.SIGNING_SECRET != ''
# bazzite pattern for an ISO file:
- run: cosign sign-blob -y --key env://SIGNING_KEY "$iso" --output-signature "${iso}.sig"
```

### ARTIFACT_UPLOAD
// SOURCE: .github/workflows/build-disk.yml (Upload step) + bazzite build_iso.yml
```yaml
- uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
  with:
    name: ${{ env.IMAGE_NAME }}-iso
    path: ${{ github.workspace }}/upload
    if-no-files-found: error
    compression-level: 0
    overwrite: true
```

### S3_UPLOAD
// SOURCE: .github/workflows/build-disk.yml (Upload to S3 step)
```yaml
- name: Upload to S3
  if: inputs.upload-to-s3 == true && github.event_name != 'pull_request'
  env:
    RCLONE_CONFIG_S3_TYPE: s3
    RCLONE_CONFIG_S3_PROVIDER: ${{ secrets.S3_PROVIDER }}
    # ...S3_ACCESS_KEY_ID / SECRET / REGION / ENDPOINT
    SOURCE_DIR: ${{ github.workspace }}/upload
  run: |
    sudo apt-get update && sudo apt-get install -y rclone
    rclone copy "$SOURCE_DIR" "S3:${{ secrets.S3_BUCKET_NAME }}/iso"
```

### ISSUE_ON_FAILURE
// SOURCE: .github/workflows/build.yml:206-244
```yaml
- name: Open tracking issue on failure
  if: failure() && github.event_name != 'pull_request'
  uses: actions/github-script@60a0d83039c74a4aee543508d2ffcb1c3799cdea # v7.0.1
  with:
    script: | # dedup by label, comment if open else create; close on success
```

### TITANOBOA_CALL (external — the new piece)
// SOURCE: ublue-os/titanoboa action.yml + bazzite build_iso.yml
```yaml
- name: Build ISO
  id: build
  uses: ublue-os/titanoboa@<PINNED_SHA>   # renovate-managed, like every other action here
  with:
    image-ref: ${{ env.IMAGE_REGISTRY }}/${{ env.IMAGE_NAME }}:${{ env.DEFAULT_TAG }}
    iso-dest: ${{ github.workspace }}/output/bazzite-tower-${{ env.DEFAULT_TAG }}-live-amd64.iso
```

---

## Files to Change

| File | Action | Justification |
|---|---|---|
| `.github/workflows/build-iso.yml` | CREATE | The titanoboa ISO workflow |
| `Justfile` | UPDATE | Add `build-iso-live` (local titanoboa run); leave broken BIB `build-iso` alone or repoint |
| `README.md` | UPDATE | Flip the "ISO builds are disabled" note to "live ISO via titanoboa"; add repo-layout row |
| `docs/CODEMAPS/ci-cd.md` | UPDATE | Add a `build-iso.yml` row to the workflow table |
| `system_files/usr/lib/bootc-image-builder/iso.yaml` | CREATE (CONDITIONAL) | Only if Task 1 spike shows the base image lacks the titanoboa ISO contract |
| `installer/Containerfile` + `installer/kde_flatpaks` | CREATE (CONDITIONAL) | Only if the spike shows the plain image can't be fed to titanoboa and the bazzite payload-builder pattern is required |

## NOT Building
- arm64 ISO (amd64 only to start; the matrix can add arm64 later)
- A GNOME live ISO variant (KDE only first; bazzite-tower is KDE-focused)
- Secure Boot signing of the ISO's bootloader/shim (separate concern from cosign blob signing)
- Auto-publishing ISOs to a GitHub Release or public CDN (artifact + optional S3 only)
- Touching `build-disk.yml` / the qcow2 path (it works; leave it)
- Reviving BIB anaconda-iso in any form

---

## Step-by-Step Tasks

### Task 1: SPIKE — does the published image build a bootable ISO with the plain action?
- **ACTION**: Run titanoboa against the already-published image and see if it (a) builds and (b) boots.
- **IMPLEMENT**: On a KVM-capable machine (the P1, or a throwaway CI dispatch of a minimal workflow): `sudo TITANOBOA_CTR_IMAGE=ghcr.io/bearyjd/bazzite-tower:latest <titanoboa>/main.sh` (or `podman run` the titanoboa builder image). Then boot the resulting `output.iso` in a VM (`qemu-system-x86_64 -enable-kvm -m 4G -cdrom output.iso`, UEFI firmware) and confirm it reaches a live session / installer.
- **MIRROR**: TITANOBOA_CALL.
- **IMPORTS**: titanoboa builder (`ghcr.io/ublue-os/titanoboa:latest`), podman, qemu.
- **GOTCHA**: If it fails with a missing `/usr/lib/bootc-image-builder/iso.yaml` or missing kernel/initramfs/grub-modules, the base does NOT satisfy the contract → switch to the payload-builder fallback (Task 1b). titanoboa is mid-revamp; `@main` and the bazzite fork `Zeglius/titanoboa@revamp-pr` may differ — note which one actually works.
- **VALIDATE**: An `output.iso` exists AND boots to a usable live/installer session in a VM. This is the gate for the whole plan; record which titanoboa ref worked.

### Task 1b: FALLBACK (only if Task 1 fails) — port the bazzite payload-builder
- **ACTION**: Create `installer/Containerfile` that assembles the titanoboa contract (mirroring bazzite `installer/`), build `localhost/payload:latest`, feed THAT to titanoboa.
- **IMPLEMENT**: Port bazzite's `installer/` with build-args `BASE_IMAGE=ghcr.io/bearyjd/bazzite-tower:latest`, `INSTALL_IMAGE_PAYLOAD=<same>`, `FLATPAK_DIR_SHORTNAME=kde_flatpaks`; add a `kde_flatpaks` list.
- **MIRROR**: bazzite `build_iso.yml` "Build Container Image" step + `installer/`.
- **GOTCHA**: This is the real complexity. If reached, re-scope to XL and consider a separate plan.
- **VALIDATE**: Same as Task 1 (ISO boots).

### Task 2: Create `.github/workflows/build-iso.yml`
- **ACTION**: New workflow, `workflow_dispatch` (inputs: `platform` default amd64, `upload-to-s3` default false) plus optional weekly `schedule`.
- **IMPLEMENT**: job on `ubuntu-latest`, `permissions: {contents: read, packages: read, id-token: write}`; steps: free space (FREE_DISK_SPACE) → checkout → lowercase prep (LOWERCASE_OWNER_PREP) → titanoboa (TITANOBOA_CALL with the ref Task 1 proved) → move to `upload/` + `sha256sum … | tee …-CHECKSUM` → cosign install + `sign-blob` (gated on `SIGNING_SECRET`, COSIGN_SIGN) → artifact upload (ARTIFACT_UPLOAD) → optional S3 (S3_UPLOAD) → issue-on-failure (ISSUE_ON_FAILURE, label `iso-failure`).
- **MIRROR**: every pattern above.
- **IMPORTS**: pinned SHAs for checkout, remove-unwanted-software, titanoboa, cosign-installer, upload-artifact, github-script (copy SHAs already used in build.yml/build-disk.yml so renovate keeps one set).
- **GOTCHA**: titanoboa needs `sudo` + container caps; the action handles caps internally but the runner must allow `sudo` (ubuntu-latest does). Do NOT add a `pull_request` build trigger — ISO builds are heavy; keep it dispatch/schedule like build-disk.yml.
- **VALIDATE**: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/build-iso.yml'))"` parses; `gh workflow run build-iso.yml -f platform=amd64` produces a green run with an `*.iso` artifact.

### Task 3: Add a local `just build-iso-live` recipe
- **ACTION**: Justfile recipe that runs titanoboa locally against the local or published image.
- **IMPLEMENT**: `build-iso-live $target_image=…:` → `sudo TITANOBOA_CTR_IMAGE="$target_image" podman run … ghcr.io/ublue-os/titanoboa:latest` (or the documented main.sh invocation Task 1 proved), output to `output/`.
- **MIRROR**: existing Justfile recipe style (`[group('Build Virtal Machine Image')]`, `$target_image` default).
- **GOTCHA**: needs sudo + KVM locally; document that. Leave the existing BIB `build-iso`/`rebuild-iso`/`run-vm-iso` recipes (they point at iso-kde.toml and hit the dead BIB path) OR repoint them with a comment — decide during impl, don't expand scope.
- **VALIDATE**: `just --list` parses; recipe runs on the P1 and yields a bootable ISO.

### Task 4: Sync docs + memory
- **ACTION**: Update README, ci-cd codemap, and the constraints memory to reflect a working titanoboa ISO path.
- **IMPLEMENT**: README "Setting Up Disk Image Builds" — change step 1 from "ISO builds are disabled" to "live ISO via `build-iso.yml` (titanoboa)"; add a `build-iso.yml` repo-layout row. ci-cd.md — add the workflow row. Memory `bazzite-tower-disk-build-constraints` — note the titanoboa path now exists.
- **MIRROR**: the doc-sync style used across this repo's recent commits.
- **GOTCHA**: keep the BIB-is-dead note; titanoboa is the replacement, not a BIB fix.
- **VALIDATE**: links resolve; no stale "ISO disabled" claims remain (`grep -rn "ISO builds are disabled" README.md` → none).

---

## Testing Strategy

### Unit Tests
N/A — this is CI/workflow + shell, no unit-testable logic. Validation is build + boot.

### Edge Cases Checklist
- [ ] `SIGNING_SECRET` absent → sign step skipped, build still green (mirror build.yml gating)
- [ ] `upload-to-s3=false` → artifact-only, no S3 calls
- [ ] titanoboa `@main` vs the bazzite fork — confirm which the spike validated; pin it
- [ ] Runner out of disk → loopback storage trick from bazzite
- [ ] ISO actually boots on UEFI in a VM (the real acceptance, not just "build green")
- [ ] Image is public (titanoboa must pull `ghcr.io/bearyjd/bazzite-tower:latest`; it is, GHCR public)

---

## Validation Commands

### Static Analysis
```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/build-iso.yml')); print('ok')"
just --list >/dev/null && echo "Justfile ok"
```
EXPECT: both print ok, no parse errors

### CI run
```bash
gh workflow run build-iso.yml -f platform=amd64 -f upload-to-s3=false
# then watch the dispatched run to green and confirm an .iso artifact
```
EXPECT: green run; `*.iso` + `*-CHECKSUM` artifact attached

### Boot verification (the one that matters)
```bash
# on a KVM host, against the produced ISO:
qemu-system-x86_64 -enable-kvm -m 4096 -bios /usr/share/OVMF/OVMF_CODE.fd -cdrom <iso>
```
EXPECT: boots to a live session / installer; can complete an install

### Manual Validation
- [ ] Download the artifact, flash to USB, boot a real machine, install, first boot succeeds (groups/SELinux OK — the thing BIB+Anaconda failed at)

---

## Acceptance Criteria
- [ ] `build-iso.yml` dispatch produces a green run with a signed, checksummed `.iso` artifact
- [ ] The ISO boots on UEFI and reaches a usable live/installer session
- [ ] An install from the ISO produces a working first boot
- [ ] Docs + memory updated; no stale "ISO disabled" claims
- [ ] Actions are SHA-pinned to match the repo's renovate setup

## Completion Checklist
- [ ] Follows the mirrored patterns (env, lowercase prep, cosign, artifact, S3, issue-on-failure)
- [ ] No `pull_request` trigger (heavy build, dispatch/schedule only)
- [ ] titanoboa ref pinned to the version the spike proved
- [ ] build-disk.yml/qcow2 untouched
- [ ] No revival of BIB anaconda-iso

## Risks
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| titanoboa is mid-revamp; `@main` API/contract shifts under us (bazzite tracks a fork branch) | High | High | Spike first (Task 1) to find the working ref; SHA-pin it; renovate watches for updates |
| Published bazzite-tower image doesn't satisfy titanoboa's ISO contract | Medium | High | Task 1b fallback: port bazzite's `installer/` payload-builder (re-scope to XL if needed) |
| ISO builds but won't boot / install (the #3418 failure class) | Medium | High | Boot-test in a VM is a hard acceptance gate; do not ship a build-green-but-unbootable ISO |
| Runner disk exhaustion (live ISOs are large) | Medium | Medium | free-disk-space step + bazzite's 70G btrfs loopback |
| Single-pass impl blocked by the spike branch | High | Medium | Plan is explicitly spike-gated; expect Task 1 to decide the path before Tasks 2-4 |

## Notes
- This plan is honestly **spike-gated**, not clean single-pass: titanoboa's active revamp + the contract unknown mean Task 1 must run before the rest is certain. That is by design, not a gap.
- Sources: titanoboa `action.yml`, bazzite `build_iso.yml`, bootc-image-builder#1188, ublue-os/bazzite#3418, this repo's `build.yml`/`build-disk.yml`, memory `bazzite-tower-disk-build-constraints`.
- Keep `iso-kde.toml`/`iso-gnome.toml` (BIB configs) as-is; the live ISO path does not use them but they document desktop intent and cost nothing.
