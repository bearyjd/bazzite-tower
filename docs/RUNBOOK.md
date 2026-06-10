# Runbook

Operational procedures for the `bazzite-tower` image. There is no server to deploy —
the "deployment" is a bootc image the target machine rebases onto. Day-2 operations
are install, update, **rollback**, health-check, triage, and the CI publish pipeline.

Image: `ghcr.io/bearyjd/bazzite-tower:latest` · signed with `cosign.pub`.

## Install / switch

```bash
sudo bootc switch ghcr.io/bearyjd/bazzite-tower:latest
sudo systemctl reboot
```

From any bootc host (Bazzite, Bluefin, Aurora, Silverblue, Fedora Atomic). Bazzite's
policy enforces signature verification against `cosign.pub`. See
[README "Installing"](../README.md#installing).

## Update

Bazzite applies image updates in the background. Force one immediately with:

```bash
sudo bootc upgrade
sudo systemctl reboot
```

`:latest` rebuilds weekly (Sun 06:00 UTC) and on every push to `main`.

## Rollback (do this first when a boot goes bad)

```bash
sudo bootc rollback        # stage the previous deployment
sudo systemctl reboot
```

Or pick the previous entry in the GRUB boot menu at power-on. To **freeze** on a
known-good build instead of tracking `:latest`, switch to a date-stamped tag:

```bash
sudo bootc switch ghcr.io/bearyjd/bazzite-tower:latest.YYYYMMDD
```

Tag scheme (`latest`, `latest.YYYYMMDD`, `YYYYMMDD`, `<short-sha>`):
[README "Tags"](../README.md#tags).

## Health checks

| Check | Command |
|---|---|
| Current/rollback deployment, signature | `bootc status` |
| Suspend mode actually in effect | `cat /sys/power/mem_sleep` (bracketed entry is active; expect `[s2idle]`) |
| Virt stack up | `systemctl is-active virtqemud.socket` · `virsh -c qemu:///system list --all` |
| Default NAT network | `ujust vm-net-status` |
| Wi-Fi diagnostics (offline) | `ujust wifi-debug` |

CI mirrors these: `tests/smoke.sh` (offline, the gate) and `tests/boot-check.sh`
(runtime). See [docs/CODEMAPS/ci-cd.md](./CODEMAPS/ci-cd.md).

## Common issues

| Symptom | Cause | Fix |
|---|---|---|
| Wi-Fi gone after a rebase | stale `wifi.backend=iwd` with iwd not enabled | the `wifi-backend-guard` service auto-recovers on boot; inspect with `ujust wifi-debug` |
| `virtqemud` won't start | upstream change dropped the `qemu` system user | rebuilt/guarded in `build.sh`; the smoke + boot tests catch regressions |
| Can't manage VMs as your user | user not yet in `kvm`/`libvirt`/`docker` | `ujust fix-vm-groups`, then re-login (the first-boot oneshot adds the first user automatically) |
| Display flicker / ~30s sluggish wake | i915 PSR/DC or `deep` suspend on Meteor Lake | baked kargs disable PSR/DC and pin `s2idle`; verify `cat /sys/power/mem_sleep` |
| Secure Boot refuses the image | — | the image kernel is signed with the shared ublue MOK (already enrolled on ublue/Bazzite hosts); no MOK work needed when switching ublue↔bazzite-tower |

Runtime surface (units, helpers, kargs): [docs/CODEMAPS/system-files.md](./CODEMAPS/system-files.md).

## Publish pipeline (operator view)

`build.yml`: build → **smoke gate** → push GHCR → cosign sign **by digest**. The gate
runs before login/push, so a broken image never reaches `:latest` (it stays last-good).
Each guard workflow opens — and later auto-closes — a labelled tracking issue:

<!-- AUTO-GENERATED:ci-labels (from .github/workflows/) -->
| Label | Workflow | Meaning |
|---|---|---|
| `ci-failure` | `build.yml` | build or smoke gate failed; nothing published |
| `boot-test-failure` | `boot-test.yml` | image built but misbehaved at runtime |
| `base-bump` | `base-watch.yml` | upstream base changed a blast-radius package |
| `iso-failure` | `build-iso.yml` | titanoboa live/installer ISO build failed |
<!-- END AUTO-GENERATED:ci-labels -->

## CI secrets

<!-- AUTO-GENERATED:secrets (from .github/workflows/) -->
| Secret | Required | Used for |
|---|---|---|
| `GITHUB_TOKEN` | auto | GHCR push; open/close tracking issues (provided by Actions) |
| `SIGNING_SECRET` | optional | cosign private key — sign image by digest (`build.yml`) and ISO sign-blob (`build-iso.yml`). Unset → signing step skipped |
| `S3_PROVIDER`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION`, `S3_ENDPOINT`, `S3_BUCKET_NAME` | optional | rclone upload of disk/ISO artifacts (`build-disk.yml`, `build-iso.yml`). Unset → artifact-only |
<!-- END AUTO-GENERATED:secrets -->

Rotate `SIGNING_SECRET` by generating a new cosign keypair, updating the repo secret,
and committing the new `cosign.pub`.

## Verify a published image

```bash
cosign verify --key cosign.pub ghcr.io/bearyjd/bazzite-tower:latest
```

## Disk / ISO artifacts

- `build-disk.yml` (dispatch) → qcow2 via bootc-image-builder → artifact or S3.
- `build-iso.yml` (dispatch, Sun 08:00 UTC) → titanoboa live/installer ISO,
  Secure-Boot-bootable → checksum + cosign sign-blob → artifact or S3. Build internals:
  [docs/CODEMAPS/iso-build.md](./CODEMAPS/iso-build.md).
